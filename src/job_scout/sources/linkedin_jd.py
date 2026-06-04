"""Fetch ONE LinkedIn job's description via the public guest endpoint.

LinkedIn rate-limits per-job description fetches hard, so this is only ever called
for a small, ranked top-N of roles (see ``enrich.py``) and the results are cached.
It hits the same guest API the search uses — no auth, no proxy — and is a polite
citizen: one try, a real browser UA, a short timeout, and it returns None on ANY
failure (the pipeline then just scores that role on title+company).

    https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}

Stdlib + requests only.
"""

from __future__ import annotations

import logging
import re
from html import unescape

log = logging.getLogger("job_scout.sources.linkedin_jd")

# Prefer the id in the known URL shapes (/jobs/view/<slug>-<id>, currentJobId=<id>,
# /jobPosting/<id>) before falling back to "first long number anywhere", so a
# numeric trackingId/refId query param can't be mistaken for the posting id.
_ID_ANCHORED = (
    re.compile(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})"),
    re.compile(r"[?&]currentJobId=(\d{6,})"),
    re.compile(r"/jobPosting/(\d{6,})"),
)
_ID_RE = re.compile(r"(\d{6,})")
# The description lives in a show-more-less-html__markup container.
_DESC_RE = re.compile(
    r'class="(?:show-more-less-html__markup|description__text)[^"]*"[^>]*>(.*?)</(?:div|section)>',
    re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

_GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 15


def job_id(url: str) -> str | None:
    """Pull the numeric posting id out of a LinkedIn job URL."""
    if not url:
        return None
    for rx in _ID_ANCHORED:
        m = rx.search(url)
        if m:
            return m.group(1)
    m = _ID_RE.search(url)
    return m.group(1) if m else None


def fetch_description(url: str, session=None) -> str | None:
    """Return the plain-text job description for a LinkedIn job URL, or None.

    Never raises — any network/parse failure yields None so the caller degrades to
    title-only scoring for that role.
    """
    jid = job_id(url)
    if not jid:
        return None
    try:
        import requests  # lazy: only needed when we actually enrich
    except Exception as e:  # noqa: BLE001
        log.warning("requests unavailable, LinkedIn enrich skipped: %s", e)
        return None

    getter = session.get if session is not None else requests.get
    try:
        resp = getter(_GUEST.format(jid=jid), headers=_HEADERS, timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        log.info("linkedin jd fetch failed (%s): %s", jid, e)
        return None
    if resp.status_code != 200 or not resp.text:
        log.info("linkedin jd %s: HTTP %s", jid, resp.status_code)
        return None

    return _parse_description(resp.text)


def _parse_description(html_text: str) -> str | None:
    m = _DESC_RE.search(html_text)
    if not m:
        return None
    text = _TAG_RE.sub(" ", m.group(1))
    text = _WS_RE.sub(" ", unescape(text)).strip()
    return text or None
