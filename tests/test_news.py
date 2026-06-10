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


def test_render_two_pane(tmp_path):
    store_p = tmp_path / "news.json"
    news_store.save({"items": {"u1": {
        "url": "https://example.com/x", "title": "AI governance goes mainstream",
        "source": "Reuters", "published": "2026-06-04T10:00:00Z", "relevance": 0.82,
        "summary": "Boards are formalizing AI oversight this quarter.\n\n"
                   "For enterprise leaders this signals budget shifting toward governance.",
        "body_text": "The full extracted article body. " * 12,
        "topic": "role-trend", "status": "new"}}}, store_p)
    html = news_report.render(store_path=store_p, html_path=tmp_path / "news.html").read_text(encoding="utf-8")
    assert "AI governance goes mainstream" in html
    assert 'href="/news"' in html and ">Jobs<" in html          # Jobs<->News nav
    assert 'id="detail"' in html and 'class="app"' in html      # two-pane layout
    assert "Boards are formalizing AI oversight" in html        # summary paragraph 1
    assert "budget shifting toward governance" in html          # summary paragraph 2
    assert "Full article text" in html                          # collapsible full-text section
    assert 'class="why"' not in html                            # the "Why" section is gone


def test_render_empty_state(tmp_path):
    out = news_report.render(store_path=tmp_path / "absent.json", html_path=tmp_path / "news.html")
    assert "No news yet" in out.read_text(encoding="utf-8")


def test_pipeline_run_gates_and_keeps(tmp_path, monkeypatch):
    """run() gathers -> dedupes -> relevance-gates -> summarizes survivors -> stores."""
    from types import SimpleNamespace

    arts = [
        {"title": "keep me", "url": "https://e.test/keep", "source": "S", "published": "", "snippet": ""},
        {"title": "drop me", "url": "https://e.test/drop", "source": "S", "published": "", "snippet": ""},
    ]
    monkeypatch.setattr(pipeline.S, "google_news_rss", lambda q, m, f: [dict(a) for a in arts])
    monkeypatch.setattr(pipeline.S, "gdelt", lambda q, m, f: [])

    def fake_rel(items, cfg):
        for a in items:
            a["relevance"] = 0.9 if "keep" in a["title"] else 0.2
            a["topic"] = "role-trend"
        return items

    def fake_sum(items, cfg):
        for a in items:
            a["summary"] = "para one\n\npara two"
            a["body_text"] = ""
        return items

    monkeypatch.setattr(pipeline, "score_relevance", fake_rel)
    monkeypatch.setattr(pipeline, "summarize", fake_sum)

    cfg = SimpleNamespace(
        news=SimpleNamespace(enabled=True, queries=["q"], max_per_query=10, freshness_hours=96,
                             relevance_threshold=0.6, block_terms=[], block_sources=[],
                             sources=SimpleNamespace(google_news=True, gdelt=False, searxng=False, searxng_url="")),
        search=SimpleNamespace(target_sectors=""))
    store_p = tmp_path / "news.json"
    summary = pipeline.run(cfg, store_path=store_p)
    assert summary["new"] == 2 and summary["kept"] == 1
    items = news_store.load(store_p)["items"]
    assert [v["title"] for v in items.values()] == ["keep me"]   # 'drop me' (0.2) gated out
    assert items["https://e.test/keep"]["summary"].count("\n\n") == 1


def test_extract_rejects_non_public_and_non_url():
    from job_scout.news import extract
    assert extract.extract_text("http://127.0.0.1/x") == ""   # SSRF guard, no network
    assert extract.extract_text("http://10.0.0.5/a") == ""
    assert extract.extract_text("not a url") == ""
    assert extract.extract_text("") == ""


def test_fetch_public_blocks_internal_without_connecting():
    # _resolve_public_ip rejects loopback/private before any socket connect.
    from job_scout import ingest
    assert ingest._resolve_public_ip("127.0.0.1") is None
    assert ingest._resolve_public_ip("10.0.0.5") is None
    assert ingest._resolve_public_ip("8.8.8.8") == "8.8.8.8"   # public literal allowed
    assert ingest.fetch_public("http://127.0.0.1:9/") is None  # never attempts the connect
    assert ingest.fetch_public("http://169.254.169.254/latest/") is None


def test_relevance_failure_drops_when_keyed(monkeypatch):
    """With an API key present, an unparseable/failed score must drop (0.0), not
    slip through the gate as None (which is the no-key digest 'keep all')."""
    from types import SimpleNamespace
    from job_scout.news import score

    from job_scout import llm
    monkeypatch.setattr(llm, "resolve_provider", lambda cfg=None, explicit=None: "anthropic")
    monkeypatch.setattr(llm, "available", lambda provider, cfg=None: True)
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "not json at all")
    cfg = SimpleNamespace(env=SimpleNamespace(anthropic_api_key="k"),
                          news=SimpleNamespace(model="m", queries=[], llm=True),
                          search=SimpleNamespace(target_sectors=""))
    arts = [{"title": "X", "snippet": "y", "url": "https://e.test/1"}]
    score.score_relevance(arts, cfg)
    assert arts[0]["relevance"] == 0.0   # keyed + failed -> drop, not keep


def test_block_filters_terms_and_sources():
    """block() drops on title/snippet term or source match, case-insensitively;
    empty block lists pass everything through untouched."""
    from types import SimpleNamespace
    from job_scout.news import pipeline

    arts = [
        {"title": "Acme Appoints Chief AI Officer", "snippet": "", "source": "Reuters"},
        {"title": "EU AI Act enforcement begins", "snippet": "", "source": "FT"},
        {"title": "Banks scale GenAI", "snippet": "wins industry award", "source": "FT"},
        {"title": "Plant automation results", "snippet": "", "source": "PR Newswire"},
    ]
    cfg = SimpleNamespace(news=SimpleNamespace(block_terms=["appoints", "award"],
                                               block_sources=["pr newswire"]))
    kept = pipeline.block(arts, cfg)
    assert [a["title"] for a in kept] == ["EU AI Act enforcement begins"]

    cfg_off = SimpleNamespace(news=SimpleNamespace(block_terms=[], block_sources=[]))
    assert pipeline.block(arts, cfg_off) == arts


def test_llm_false_skips_scoring_and_summary_llm(monkeypatch):
    """news.llm: false must never call the provider — relevance stays None (keep-all
    digest path) and summaries come from the body/snippet fallback, even with a key."""
    from types import SimpleNamespace
    from job_scout.news import score

    from job_scout import llm
    monkeypatch.setattr(llm, "resolve_provider", lambda cfg=None, explicit=None: "anthropic")
    monkeypatch.setattr(llm, "available", lambda provider, cfg=None: True)

    def boom(*a, **k):
        raise AssertionError("llm.complete must not be called when news.llm is false")

    monkeypatch.setattr(llm, "complete", boom)
    monkeypatch.setattr(score, "extract_text", lambda url: "Body para.\n\nMore body.")
    cfg = SimpleNamespace(env=SimpleNamespace(anthropic_api_key="k"),
                          news=SimpleNamespace(model="m", queries=[], llm=False),
                          search=SimpleNamespace(target_sectors=""))
    arts = [{"title": "X", "snippet": "y", "url": "https://e.test/1"}]
    score.score_relevance(arts, cfg)
    assert arts[0]["relevance"] is None and arts[0]["topic"] == "other"
    score.summarize(arts, cfg)
    assert arts[0]["summary"]   # fallback summary built without the LLM
