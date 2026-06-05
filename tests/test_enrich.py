"""Enrich-stage tests — cache-first, capped, gated, injectable fetcher.

Network is never touched: a stub fetch_fn stands in for linkedin_jd, and the cache
path is monkeypatched to a tmp file. resume_text is left empty so ranking is a
no-op (original order, no embedding model load) — keeps tests fast and hermetic.
"""

from __future__ import annotations

import json

import pytest

from job_scout import enrich
from job_scout.models import Job
from job_scout.config import (
    Config, SearchCfg, CompaniesCfg, ScoringCfg, SourcesCfg, BoardsCfg, EnvCfg,
)


def _config(*, enabled=True, max_n=30):
    return Config(
        search=SearchCfg(), companies=CompaniesCfg(), scoring=ScoringCfg(),
        sources=SourcesCfg(boards=BoardsCfg(
            linkedin_fetch_description=enabled, linkedin_enrich_max=max_n)),
        env=EnvCfg(), resume_text="",  # empty -> ranking is original-order, no ST load
    )


def _li(id, title="AI Director", desc=""):
    return Job(id=id, title=title, company="Acme",
               url=f"https://www.linkedin.com/jobs/view/{id}0000",
               source="linkedin", description=desc)


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(enrich, "_CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(enrich, "_delay", lambda: None)  # no sleeping in tests
    return tmp_path


def test_disabled_is_noop():
    jobs = [_li("1")]
    enrich.enrich_descriptions(jobs, _config(enabled=False), fetch_fn=lambda u: "X")
    assert jobs[0].description == ""


def test_fetches_and_caches(_tmp_cache):
    calls = []

    def fake(url):
        calls.append(url)
        return "Full JD for " + url[-5:]

    jobs = [_li("111111"), _li("222222")]
    enrich.enrich_descriptions(jobs, _config(), fetch_fn=fake)
    assert all(j.description.startswith("Full JD") for j in jobs)
    assert len(calls) == 2
    cache = json.loads((_tmp_cache / "cache.json").read_text())
    assert cache["111111"].startswith("Full JD")


def test_cache_hit_skips_fetch(_tmp_cache):
    (_tmp_cache / "cache.json").write_text(json.dumps({"111111": "cached JD"}))
    calls = []
    jobs = [_li("111111")]
    enrich.enrich_descriptions(jobs, _config(), fetch_fn=lambda u: calls.append(u))
    assert jobs[0].description == "cached JD"
    assert calls == []  # served from cache, no fetch


def test_respects_cap():
    calls = []
    jobs = [_li(str(i) * 6) for i in range(5)]
    enrich.enrich_descriptions(jobs, _config(max_n=2),
                               fetch_fn=lambda u: calls.append(u) or "JD")
    assert len(calls) == 2  # only top-2 (cap) fetched


def test_skips_non_linkedin_and_already_described():
    jobs = [
        Job(id="a", title="X", company="Y", url="u", source="greenhouse"),
        _li("333333", desc="already here"),
    ]
    called = []
    enrich.enrich_descriptions(jobs, _config(), fetch_fn=lambda u: called.append(u))
    assert called == []  # neither is an undescribed linkedin role
