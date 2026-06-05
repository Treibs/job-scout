"""News pipeline — gather (free sources) -> dedupe -> score -> store.

Mirrors the job pipeline. Skips already-seen URLs (no re-scoring) to save tokens,
keeps only articles >= relevance_threshold (unscored items are kept when there's no
API key, i.e. digest mode), and upserts into state/news.json preserving feedback.
"""

from __future__ import annotations

import logging

from rapidfuzz import fuzz

from . import sources as S
from . import store as STORE
from .score import score_relevance, summarize

log = logging.getLogger("job_scout.news.pipeline")


def build_queries(config) -> list[str]:
    if config.news.queries:
        return list(config.news.queries)
    # fallback: a few sector-derived queries from target_sectors
    sec = (config.search.target_sectors or "").replace("—", ",").replace("/", ",")
    terms = [t.strip() for t in sec.split(",") if t.strip()][:6]
    return [f"AI {t}" for t in terms] or ["enterprise AI adoption"]


def gather(config) -> list[dict]:
    n = config.news
    out: list[dict] = []
    for q in build_queries(config):
        if n.sources.google_news:
            out += S.google_news_rss(q, n.max_per_query, n.freshness_hours)
        if n.sources.gdelt:
            out += S.gdelt(q, n.max_per_query, n.freshness_hours)
        if n.sources.searxng:
            out += S.searxng(q, n.sources.searxng_url, n.max_per_query)
    return out


def dedupe(articles: list[dict]) -> list[dict]:
    seen_url: set[str] = set()
    kept: list[dict] = []
    for a in articles:
        cu = S.canonical_url(a.get("url", ""))
        if not cu or cu in seen_url:
            continue
        t = (a.get("title") or "").lower()
        if t and any(fuzz.token_set_ratio(t, (k.get("title") or "").lower()) >= 92 for k in kept):
            continue
        a["url"] = cu
        seen_url.add(cu)
        kept.append(a)
    return kept


def run(config, store_path=None) -> dict:
    """Full run: gather -> dedupe -> relevance gate -> (kept) extract + summarize -> store."""
    if not config.news.enabled:
        return {"enabled": False}
    raw = gather(config)
    deduped = dedupe(raw)
    store = STORE.load(store_path)
    seen = STORE.seen_urls(store)
    fresh = [a for a in deduped if a["url"] not in seen]
    score_relevance(fresh, config)
    threshold = config.news.relevance_threshold
    kept = [a for a in fresh if a.get("relevance") is None or a["relevance"] >= threshold]
    summarize(kept, config)  # extract full text + 2-paragraph summary for survivors only
    STORE.upsert(store, kept, store_path)
    return {"enabled": True, "fetched": len(raw), "deduped": len(deduped),
            "new": len(fresh), "kept": len(kept), "total": len(store.get("items", {}))}


def enrich_missing(config, store_path=None, limit=None) -> dict:
    """Upgrade already-cached items that predate the 2-paragraph summary (no
    ``body_text`` yet): extract + summarize them, preserving everything else."""
    store = STORE.load(store_path)
    todo = [it for it in store.get("items", {}).values() if "body_text" not in it]
    if limit:
        todo = todo[:limit]
    if todo:
        summarize(todo, config)  # mutates the dicts (they're refs into the store)
        STORE.save(store, store_path)
    return {"enriched": len(todo)}
