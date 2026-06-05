"""Best-effort article full-text extraction (trafilatura).

Used to give the LLM the actual article body for a richer 2-paragraph summary, and
to show the full text in the detail pane. Best-effort by design: paywalled sites,
bot blocks, and Google-News *redirect* links often won't yield clean text — those
return '' and the caller falls back to the headline+snippet. Never raises.

We fetch with our own ``requests`` (so we control timeout + UA) and feed the HTML to
``trafilatura.extract``; the URL is SSRF-guarded with the same check as URL ingest.
"""

from __future__ import annotations

import logging

from ..ingest import fetch_public

log = logging.getLogger("job_scout.news.extract")

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
_TIMEOUT = 15
_MIN_CHARS = 250   # shorter than this = likely a consent/redirect stub, treat as a miss


def extract_text(url: str, max_chars: int = 6000) -> str:
    """Return the article's plain-text body, or '' if it can't be cleanly extracted."""
    url = (url or "").strip()
    if not url.lower().startswith(("http://", "https://")):
        return ""
    try:
        import trafilatura
    except ImportError:  # pragma: no cover - trafilatura is a declared dependency
        return ""
    r = fetch_public(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)  # SSRF-safe (per-hop validated)
    if r is None or not r.text:
        return ""
    try:
        text = trafilatura.extract(r.text, include_comments=False, include_tables=False, no_fallback=True)
    except Exception as e:  # noqa: BLE001 — extraction is best-effort, never fatal
        log.info("extract parse failed for %s: %s", url, e)
        return ""
    text = (text or "").strip()
    if len(text) < _MIN_CHARS:
        return ""
    return text[:max_chars]
