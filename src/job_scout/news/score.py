"""News relevance scoring — one LLM call per new article (MiniMax via the
Anthropic-compatible endpoint, same plumbing as job scoring).

Given the article + the user's focus (resume excerpt, news queries, target sectors),
returns: relevance 0-1, a 1-2 sentence summary, a one-line why_relevant, and a topic
tag. Per the user's choice the focus is domain/role TRENDS + target SECTORS — not
company news or hiring signals — so the prompt rewards those and down-weights the rest.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from .._jsonutil import extract_json
from .store import normalize_topic

log = logging.getLogger("job_scout.news.score")

_MAX_TOKENS = 4096   # MiniMax is a reasoning model — leave room for its thinking block
_CONCURRENCY = 6


def score_articles(articles: list[dict], config) -> list[dict]:
    """Attach relevance/summary/why_relevant/topic to each article in place.
    No API key -> returns them unscored (relevance left as None)."""
    if not articles:
        return articles
    api_key = config.env.anthropic_api_key
    if not api_key:
        log.info("news scoring skipped: ANTHROPIC_API_KEY not set (%d unscored)", len(articles))
        for a in articles:
            a.setdefault("relevance", None)
        return articles
    try:
        import anthropic  # type: ignore
        client = anthropic.Anthropic(api_key=api_key)
    except Exception as e:  # noqa: BLE001
        log.warning("anthropic unavailable, news left unscored: %s", e)
        for a in articles:
            a.setdefault("relevance", None)
        return articles

    model = config.news.model
    resume = (config.resume_text or "")[:3000]
    sectors = config.search.target_sectors or ""
    topics = ", ".join(config.news.queries or [])

    def one(a):
        try:
            return _score_one(client, model, a, resume, sectors, topics)
        except Exception as e:  # noqa: BLE001 — one bad article never kills the batch
            log.warning("news scoring error for %r: %s", a.get("title"), e)
            return None

    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as ex:
        results = list(ex.map(one, articles))

    for a, r in zip(articles, results):
        if r:
            a["relevance"] = _clamp(r.get("relevance"))
            a["summary"] = str(r.get("summary") or "").strip()
            a["why_relevant"] = str(r.get("why_relevant") or "").strip()
            a["topic"] = normalize_topic(r.get("topic"))
        else:
            a["relevance"] = None
    return articles


def _score_one(client, model, a, resume, sectors, topics):
    system = (
        "You curate a career-intelligence news feed for a senior professional. Rate how "
        "RELEVANT an article is to their focus. RELEVANT = domain/role TRENDS they track "
        "(e.g. AI leadership, transformation, enterprise adoption, governance) AND their "
        "TARGET SECTORS. NOT relevant = generic product launches, unrelated earnings, "
        "job/hiring listings, or off-topic news. Be strict: 0.6+ should genuinely inform "
        "their thinking or conversations in the field.\n"
        'Return STRICT JSON only, no prose: {"relevance": <0..1>, '
        '"summary": "1-2 sentences", "why_relevant": "one line tying it to their focus", '
        '"topic": "role-trend|sector|other"}'
    )
    user = (
        f"=== THEIR FOCUS ===\nTopics: {topics}\nTarget sectors: {sectors}\n"
        f"Resume excerpt:\n{resume}\n\n"
        f"=== ARTICLE ===\nTitle: {a.get('title')}\nSource: {a.get('source')}\n"
        f"Snippet: {a.get('snippet') or '(none)'}\n\nScore it now as strict JSON."
    )
    resp = client.messages.create(model=model, max_tokens=_MAX_TOKENS, system=system,
                                  messages=[{"role": "user", "content": user}])
    text = "".join(
        getattr(b, "text", "") for b in getattr(resp, "content", []) or []
        if getattr(b, "type", None) == "text" or getattr(b, "text", None)
    )
    return extract_json(text)


def _clamp(v):
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return None
