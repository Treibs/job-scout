"""News subsystem tests — RSS parsing (incl. XML-hardening), dedupe, store
upsert/feedback, query building, and page render. No network, no LLM."""

from __future__ import annotations

from types import SimpleNamespace

from job_scout.news import pipeline, sources
from job_scout.news import store as news_store
from job_scout.sinks import news_report

_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>AI transformation in banking accelerates</title>
  <link>https://example.com/a?utm_source=google&amp;id=5</link>
  <pubDate>Wed, 04 Jun 2026 10:00:00 GMT</pubDate>
  <description>&lt;p&gt;Banks adopt AI at scale&lt;/p&gt;</description>
  <source url="https://reuters.com">Reuters</source>
</item>
<item>
  <title>Half record</title>
</item>
</channel></rss>"""

# A malicious feed: a custom entity (billion-laughs / XXE vector).
_EVIL = b"""<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "BOOM">]>
<rss><channel><item><title>&a;</title><link>http://x/1</link></item></channel></rss>"""


def test_parse_google_rss():
    out = sources.parse_google_rss(_RSS)
    assert len(out) == 1  # the half-record (no link) is dropped
    a = out[0]
    assert a["title"] == "AI transformation in banking accelerates"
    assert a["source"] == "Reuters"
    assert a["snippet"] == "Banks adopt AI at scale"
    assert a["published"].startswith("2026-06-04")


def test_parse_google_rss_rejects_entity_xml():
    # defusedxml must refuse custom entities -> we return [] rather than expand them.
    assert sources.parse_google_rss(_EVIL) == []


def test_canonical_url_strips_tracking():
    assert sources.canonical_url("https://x.com/a?utm_source=g&id=5&fbclid=zz") == "https://x.com/a?id=5"
    assert sources.canonical_url("https://x.com/a") == "https://x.com/a"


def test_dedupe_url_and_fuzzy_title():
    arts = [
        {"title": "AI leaders reshape enterprise strategy", "url": "https://x.com/a?utm_source=g"},
        {"title": "AI leaders reshape enterprise strategy!", "url": "https://x.com/a?id=2"},  # same canonical? no
        {"title": "AI leaders reshape the enterprise strategy", "url": "https://y.com/b"},     # fuzzy dup title
        {"title": "Totally different headline about manufacturing", "url": "https://z.com/c"},
    ]
    out = pipeline.dedupe(arts)
    titles = [a["title"] for a in out]
    # first kept; the y.com near-duplicate title collapses; the manufacturing one stays
    assert "Totally different headline about manufacturing" in titles
    assert len(out) <= 3
    assert out[0]["url"] == "https://x.com/a"  # canonicalized (utm stripped)


def test_store_upsert_preserves_feedback(tmp_path):
    p = tmp_path / "news.json"
    store = news_store.load(p)
    news_store.upsert(store, [{"url": "u1", "title": "T1", "relevance": 0.8}], p)
    # user marks feedback
    assert news_store.update_feedback("u1", {"useful": "up", "status": "saved"}, p) is True
    # a later run refreshes metadata but must NOT clobber feedback
    store = news_store.load(p)
    news_store.upsert(store, [{"url": "u1", "title": "T1 updated", "relevance": 0.9}], p)
    item = news_store.load(p)["items"]["u1"]
    assert item["title"] == "T1 updated" and item["relevance"] == 0.9
    assert item["useful"] == "up" and item["status"] == "saved"
    assert "first_seen" in item


def test_store_update_feedback_unknown_url(tmp_path):
    p = tmp_path / "news.json"
    news_store.upsert(news_store.load(p), [{"url": "u1", "title": "T"}], p)
    assert news_store.update_feedback("nope", {"useful": "up"}, p) is False


def test_items_sorted_dismissed_last(tmp_path):
    store = {"items": {
        "a": {"url": "a", "published": "2026-06-01T00:00:00Z", "status": "new"},
        "b": {"url": "b", "published": "2026-06-05T00:00:00Z", "status": "dismissed"},
        "c": {"url": "c", "published": "2026-06-03T00:00:00Z", "status": "saved"},
    }}
    order = [x["url"] for x in news_store.items_sorted(store)]
    assert order == ["c", "a", "b"]  # newest active first (c>a), dismissed (b) last


def test_build_queries_uses_config_then_fallback():
    cfg = SimpleNamespace(news=SimpleNamespace(queries=["q1", "q2"]),
                          search=SimpleNamespace(target_sectors=""))
    assert pipeline.build_queries(cfg) == ["q1", "q2"]
    cfg2 = SimpleNamespace(news=SimpleNamespace(queries=[]),
                           search=SimpleNamespace(target_sectors="banking, manufacturing"))
    assert pipeline.build_queries(cfg2) == ["AI banking", "AI manufacturing"]


def test_render_writes_feed(tmp_path):
    store_p = tmp_path / "news.json"
    news_store.save({"items": {"u1": {
        "url": "https://example.com/x", "title": "AI governance goes mainstream",
        "source": "Reuters", "published": "2026-06-04T10:00:00Z", "relevance": 0.82,
        "summary": "Boards push AI governance.", "why_relevant": "ties to your AI leadership focus",
        "topic": "role-trend", "status": "new"}}}, store_p)
    out = news_report.render(store_path=store_p, html_path=tmp_path / "news.html")
    html = out.read_text(encoding="utf-8")
    assert "AI governance goes mainstream" in html
    assert 'href="/news"' in html and ">Jobs<" in html  # the nav
    assert "ties to your AI leadership focus" in html


def test_render_empty_state(tmp_path):
    out = news_report.render(store_path=tmp_path / "absent.json", html_path=tmp_path / "news.html")
    assert "No news yet" in out.read_text(encoding="utf-8")
