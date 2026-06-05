"""News relevance scoring + 2-paragraph summarization (MiniMax via the
Anthropic-compatible endpoint, same plumbing as job scoring).

Two stages, so extraction + summarization is only paid for articles that pass the
relevance gate:

  score_relevance(articles) -> relevance (0-1) + topic, from title+snippet (cheap)
  summarize(kept)           -> body_text (best-effort full text) + a TWO-paragraph
                               summary (from the body when available, else the
                               headline+snippet)

Focus = domain/role TRENDS + target SECTORS (the user's choice).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from .._jsonutil import extract_json
from .extract import extract_text
from .store import normalize_topic

log = logging.getLogger("job_scout.news.score")

_MAX_TOKENS = 4096   # MiniMax is a reasoning model — leave room for its thinking block
_CONCURRENCY = 6


def _client(config):
    if not config.env.anthropic_api_key:
        return None
    try:
        import anthropic  # type: ignore
        return anthropic.Anthropic(api_key=config.env.anthropic_api_key)
    except Exception as e:  # noqa: BLE001
        log.warning("anthropic unavailable: %s", e)
        return None


def _resp_text(resp) -> str:
    return "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or []
                   if getattr(b, "type", None) == "text" or getattr(b, "text", None))


# ── stage 1: relevance gate ──────────────────────────────────────────────────
def score_relevance(articles: list[dict], config) -> list[dict]:
    """Attach relevance (0-1) + topic from title+snippet. No key -> relevance None."""
    if not articles:
        return articles
    client = _client(config)
    if client is None:
        for a in articles:
            a.setdefault("relevance", None)
            a.setdefault("topic", "other")
        return articles

    model, sectors = config.news.model, (config.search.target_sectors or "")
    topics = ", ".join(config.news.queries or [])

    def one(a):
        try:
            return _relevance_one(client, model, a, sectors, topics)
        except Exception as e:  # noqa: BLE001
            log.warning("relevance error %r: %s", a.get("title"), e)
            return None

    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        results = list(ex.map(one, articles))
    for a, r in zip(articles, results):
        rel = _clamp(r.get("relevance")) if r else None
        # A key IS present here, so a failed/unparseable score must DROP (0.0), not
        # slip through the gate — `relevance is None` is reserved for the no-key
        # digest path above (which intentionally keeps everything).
        a["relevance"] = rel if rel is not None else 0.0
        a["topic"] = normalize_topic(r.get("topic")) if r else "other"
    return articles


def _relevance_one(client, model, a, sectors, topics):
    system = (
        "You gate a career-intelligence news feed for a senior professional. Rate how "
        "RELEVANT an article is to their focus. RELEVANT = domain/role TRENDS (AI leadership, "
        "transformation, enterprise adoption, governance) AND their TARGET SECTORS. NOT relevant "
        "= generic product launches, unrelated earnings, job/hiring listings, off-topic. Be strict.\n"
        'Return STRICT JSON only: {"relevance": <0..1>, "topic": "role-trend|sector|other"}'
    )
    user = (f"Topics: {topics}\nTarget sectors: {sectors}\n\nTitle: {a.get('title')}\n"
            f"Source: {a.get('source')}\nSnippet: {a.get('snippet') or '(none)'}\n\nScore as strict JSON.")
    resp = client.messages.create(model=model, max_tokens=_MAX_TOKENS, system=system,
                                  messages=[{"role": "user", "content": user}])
    return extract_json(_resp_text(resp))


# ── stage 2: extract + 2-paragraph summary ───────────────────────────────────
def summarize(articles: list[dict], config) -> list[dict]:
    """For each article: best-effort full-text extraction + a 2-paragraph summary,
    set in place (``body_text`` + ``summary``). Works without a key (snippet/body
    fallback for the summary)."""
    if not articles:
        return articles
    client = _client(config)
    model, sectors = config.news.model, (config.search.target_sectors or "")
    topics = ", ".join(config.news.queries or [])

    def one(a):
        body = extract_text(a.get("url", ""))
        a["body_text"] = body
        try:
            a["summary"] = _summarize_one(client, model, a, body, sectors, topics)
        except Exception as e:  # noqa: BLE001
            log.warning("summary error %r: %s", a.get("title"), e)
            a["summary"] = _fallback_summary(a, body)
        return a

    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        list(ex.map(one, articles))
    return articles


def _summarize_one(client, model, a, body, sectors, topics):
    if client is None:
        return _fallback_summary(a, body)
    source_text = body if body else (a.get("snippet") or "")
    src_label = ("ARTICLE TEXT" if body
                 else "HEADLINE + SNIPPET (full text unavailable; infer carefully, don't invent specifics)")
    system = (
        "Summarize this news article for a senior professional's intelligence feed in EXACTLY TWO "
        "short paragraphs of plain text (separate them with a blank line; no markdown, no headers).\n"
        "Paragraph 1: what happened — the substance.\n"
        "Paragraph 2: why it matters for someone focused on these themes: "
        f"{topics}; and these sectors: {sectors}. Be concrete; skip fluff."
    )
    user = (f"Title: {a.get('title')}\nSource: {a.get('source')}\n\n"
            f"=== {src_label} ===\n{source_text[:6000]}\n\nWrite the two-paragraph summary now.")
    resp = client.messages.create(model=model, max_tokens=_MAX_TOKENS, system=system,
                                  messages=[{"role": "user", "content": user}])
    return _resp_text(resp).strip() or _fallback_summary(a, body)


def _fallback_summary(a, body):
    if body:
        paras = [p.strip() for p in body.split("\n") if p.strip()]
        return "\n\n".join(paras[:2])[:700]
    return (a.get("snippet") or a.get("title") or "").strip()


def _clamp(v):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None
