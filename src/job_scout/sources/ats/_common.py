"""Shared helpers for the ATS source adapters.

Every ATS adapter hits a public JSON endpoint and then does the same handful of
chores: send a request with one light retry, flatten an HTML description to text,
parse an ISO timestamp, and decide whether a posting is fresh or remote. Those
lived copy-pasted in each adapter — they live here once now.

Two request styles are offered because the adapters genuinely want both:
``get``/``post`` raise on failure (the caller wraps the whole fetch in try/except),
while ``try_get``/``try_post`` return None and log (the caller skips that company).
"""

from __future__ import annotations

import html
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import requests

log = logging.getLogger("job_scout.sources.ats")

# Browser-ish UA — these are public endpoints; we identify like a normal client.
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 15

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_REMOTE_RE = re.compile(r"\bremote\b", re.I)


# ── HTTP (one light retry) ───────────────────────────────────────────────────
def _headers(has_body: bool) -> dict:
    h = {"User-Agent": UA, "Accept": "application/json"}
    if has_body:
        h["Content-Type"] = "application/json"
    return h


def request(method: str, url: str, *, params: dict | None = None,
            json_body: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> requests.Response:
    """One HTTP request with a single light retry (1s backoff) on transient
    failure. Raises the last ``requests`` exception if both attempts fail."""
    headers = _headers(json_body is not None)
    last_exc: Exception | None = None
    for attempt in range(2):  # original + one retry
        try:
            resp = requests.request(method, url, params=params, json=json_body,
                                    headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt == 0:
                time.sleep(1.0)  # light backoff
    assert last_exc is not None
    raise last_exc


def get(url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT) -> requests.Response:
    """GET that raises on failure."""
    return request("GET", url, params=params, timeout=timeout)


def post(url: str, json_body: dict, timeout: int = DEFAULT_TIMEOUT) -> requests.Response:
    """POST a JSON body; raises on failure."""
    return request("POST", url, json_body=json_body, timeout=timeout)


def try_get(url: str, params: dict | None = None, timeout: int = DEFAULT_TIMEOUT,
            what: str = "GET") -> requests.Response | None:
    """GET that returns None (and logs) instead of raising."""
    try:
        return get(url, params=params, timeout=timeout)
    except Exception as e:  # noqa: BLE001 — sources are untrusted; isolate per-company
        log.warning("%s failed (%s): %s", what, url, e)
        return None


def try_post(url: str, json_body: dict, timeout: int = DEFAULT_TIMEOUT,
             what: str = "POST") -> requests.Response | None:
    """POST that returns None (and logs) instead of raising."""
    try:
        return post(url, json_body=json_body, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        log.warning("%s failed (%s): %s", what, url, e)
        return None


# ── text + dates ─────────────────────────────────────────────────────────────
def strip_html(raw: str | None) -> str | None:
    """Flatten an HTML description to readable plain text."""
    if not raw:
        return None
    text = _WS_RE.sub(" ", _TAG_RE.sub(" ", html.unescape(raw)))
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip() or None


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp; tolerates a trailing 'Z' and date-only input.
    Naive datetimes are assumed UTC. Returns None when nothing parses."""
    if not isinstance(value, str) or not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s[:10])  # date-only fallback
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def is_fresh(date_posted: Any, freshness_hours: int | None) -> bool:
    """True if within the freshness window. Missing/unparseable date -> keep
    (we don't drop records just because a date is absent)."""
    if not freshness_hours or freshness_hours <= 0:
        return True
    dt = parse_iso(date_posted)
    if dt is None:
        return True
    age_h = (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0
    return age_h <= freshness_hours


def is_remote_text(location: str | None) -> bool | None:
    """Whether a location string denotes remote (word-boundary, so 'Claremont'
    doesn't match). None when there's no location to judge."""
    if not location:
        return None
    return bool(_REMOTE_RE.search(location))
