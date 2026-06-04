"""Tests for the shared ATS helpers (sources/ats/_common.py).

The five ATS adapters share these — they had no unit coverage before the helpers
were extracted, so this locks in the behavior the adapters rely on.
"""

from __future__ import annotations

import requests

from job_scout.sources.ats import _common


def test_strip_html():
    # Tags become spaces, entities are unescaped, whitespace collapses.
    assert _common.strip_html("<p>Hi&amp;<b>bye</b></p>") == "Hi& bye"
    assert _common.strip_html(None) is None
    assert _common.strip_html("   ") is None


def test_parse_iso():
    assert _common.parse_iso("2026-06-04T10:00:00Z").year == 2026
    assert _common.parse_iso("2026-06-04").year == 2026  # date-only fallback
    assert _common.parse_iso("garbage") is None
    assert _common.parse_iso(None) is None
    # Naive timestamps are treated as UTC.
    assert _common.parse_iso("2026-06-04T10:00:00").tzinfo is not None


def test_is_remote_text():
    assert _common.is_remote_text("Remote - US") is True
    assert _common.is_remote_text("Claremont, CA") is False  # word boundary, not substring
    assert _common.is_remote_text("") is None
    assert _common.is_remote_text(None) is None


def test_is_fresh():
    assert _common.is_fresh(None, 72) is True                     # missing date -> keep
    assert _common.is_fresh("garbage", 72) is True                # unparseable -> keep
    assert _common.is_fresh("2000-01-01T00:00:00Z", 72) is False  # ancient -> drop
    assert _common.is_fresh("2000-01-01T00:00:00Z", 0) is True    # no window -> keep


class _Resp:
    def __init__(self, ok: bool = True):
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise requests.HTTPError("boom")


def test_get_retries_once_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_request(method, url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("transient")
        return _Resp(ok=True)

    monkeypatch.setattr(_common.requests, "request", fake_request)
    monkeypatch.setattr(_common.time, "sleep", lambda *_a, **_k: None)
    resp = _common.get("https://example.test/jobs")
    assert calls["n"] == 2 and isinstance(resp, _Resp)


def test_try_get_returns_none_on_persistent_failure(monkeypatch):
    def fake_request(method, url, **kw):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(_common.requests, "request", fake_request)
    monkeypatch.setattr(_common.time, "sleep", lambda *_a, **_k: None)
    assert _common.try_get("https://example.test/jobs") is None
