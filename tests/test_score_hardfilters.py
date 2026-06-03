"""Hard-filter-only tests for score.py.

We exercise stage 1 (deterministic hard filters) in isolation:
  · no ANTHROPIC_API_KEY  -> stage 3 (LLM) is skipped, jobs returned unscored
  · pre_filter disabled    -> stage 2 is a no-op

score.py lazy-imports sentence-transformers / anthropic, so importing the module
is dependency-light. pydantic is required to build Config; skip if absent.
"""

from __future__ import annotations

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("job_scout.score")

from job_scout.score import score_jobs
from job_scout.models import Job
from job_scout.config import (
    Config,
    SearchCfg,
    LocationCfg,
    HardFilters,
    CompaniesCfg,
    ScoringCfg,
    PreFilterCfg,
    SourcesCfg,
    EnvCfg,
)


def make_config(
    *,
    remote_policy="include",
    seniority=None,
    role_fit_gate=True,
    exclude_industries=None,
    exclude_keywords=None,
    include_keywords=None,
):
    """Build a Config with no LLM key and the pre-filter disabled, so score_jobs
    reduces to just the hard-filter stage."""
    return Config(
        search=SearchCfg(
            keywords=[],
            location=LocationCfg(query="Chicago, IL", remote_policy=remote_policy),
            seniority=seniority or [],
            hard_filters=HardFilters(
                exclude_industries=exclude_industries or [],
                exclude_keywords=exclude_keywords or [],
                include_keywords=include_keywords or [],
            ),
        ),
        companies=CompaniesCfg(companies=[]),
        scoring=ScoringCfg(
            dimensions=[],
            role_fit_gate=role_fit_gate,
            pre_filter=PreFilterCfg(enabled=False, method="none"),
        ),
        sources=SourcesCfg(),
        env=EnvCfg(anthropic_api_key=None),  # no key -> no LLM
        resume_text="",
    )


def _job(title="Director AI", company="Acme", description="", is_remote=None, source="indeed", url=None):
    return Job(
        id="x",
        title=title,
        company=company,
        url=url or f"https://x/{title}".replace(" ", "_"),
        source=source,
        is_remote=is_remote,
        description=description,
    )


def titles(jobs):
    return [j.title for j in jobs]


# ── no-LLM passthrough ───────────────────────────────────────────────────────
def test_no_key_returns_jobs_unscored():
    cfg = make_config()
    jobs = [_job("Director AI"), _job("Director Data")]
    out = score_jobs(jobs, cfg)
    assert len(out) == 2
    assert all(j.score is None for j in out)


def test_empty_jobs_returns_empty():
    assert score_jobs([], make_config()) == []


# ── remote policy ────────────────────────────────────────────────────────────
def test_remote_only_drops_onsite():
    cfg = make_config(remote_policy="only")
    jobs = [
        _job("Remote Role", is_remote=True),
        _job("Onsite Role", is_remote=False),
        _job("Unknown Role", is_remote=None),  # unknown kept (lenient)
    ]
    out = score_jobs(jobs, cfg)
    out_titles = titles(out)
    assert "Remote Role" in out_titles
    assert "Onsite Role" not in out_titles
    assert "Unknown Role" in out_titles


def test_remote_exclude_drops_remote():
    cfg = make_config(remote_policy="exclude")
    jobs = [
        _job("Remote Role", is_remote=True),
        _job("Onsite Role", is_remote=False),
        _job("Unknown Role", is_remote=None),
    ]
    out_titles = titles(score_jobs(jobs, cfg))
    assert "Remote Role" not in out_titles
    assert "Onsite Role" in out_titles
    assert "Unknown Role" in out_titles


def test_remote_include_keeps_all():
    cfg = make_config(remote_policy="include")
    jobs = [
        _job("Remote Role", is_remote=True),
        _job("Onsite Role", is_remote=False),
    ]
    assert len(score_jobs(jobs, cfg)) == 2


# ── exclude industries / keywords ────────────────────────────────────────────
def test_exclude_industries_substring_match():
    cfg = make_config(exclude_industries=["healthcare", "biotech"])
    jobs = [
        _job("Director AI", company="HealthCare Inc", description="hospital systems"),
        _job("Director AI", company="Fintech Co", description="payments platform"),
    ]
    out = score_jobs(jobs, cfg)
    assert [j.company for j in out] == ["Fintech Co"]


def test_exclude_keywords_kill_word():
    cfg = make_config(exclude_keywords=["unpaid"])
    jobs = [
        _job("Intern Role", description="this is an unpaid internship"),
        _job("Director AI", description="great paid role"),
    ]
    out_titles = titles(score_jobs(jobs, cfg))
    assert "Intern Role" not in out_titles
    assert "Director AI" in out_titles


# ── include keywords ─────────────────────────────────────────────────────────
def test_include_keywords_requires_at_least_one():
    cfg = make_config(include_keywords=["python", "machine learning"])
    jobs = [
        _job("ML Lead", description="deep python expertise required"),
        _job("Sales Lead", description="quota-carrying sales role"),
    ]
    out_titles = titles(score_jobs(jobs, cfg))
    assert "ML Lead" in out_titles
    assert "Sales Lead" not in out_titles


def test_empty_include_keywords_keeps_all():
    cfg = make_config(include_keywords=[])
    jobs = [_job("Anything", description="whatever")]
    assert len(score_jobs(jobs, cfg)) == 1


# ── seniority gate ───────────────────────────────────────────────────────────
def test_seniority_gate_matches_title_and_synonyms():
    cfg = make_config(seniority=["director"], role_fit_gate=True)
    jobs = [
        _job("Director of AI"),                 # direct match
        _job("Head of AI Strategy"),            # synonym of director
        _job("Junior AI Analyst"),              # no match -> dropped
    ]
    out_titles = titles(score_jobs(jobs, cfg))
    assert "Director of AI" in out_titles
    assert "Head of AI Strategy" in out_titles
    assert "Junior AI Analyst" not in out_titles


def test_seniority_gate_disabled_when_role_fit_gate_false():
    cfg = make_config(seniority=["director"], role_fit_gate=False)
    jobs = [_job("Junior AI Analyst")]
    # Gate off -> not dropped on seniority.
    assert len(score_jobs(jobs, cfg)) == 1


def test_seniority_gate_disabled_when_no_seniority_configured():
    cfg = make_config(seniority=[], role_fit_gate=True)
    jobs = [_job("Junior AI Analyst")]
    assert len(score_jobs(jobs, cfg)) == 1


def test_filters_combine():
    cfg = make_config(
        remote_policy="exclude",
        seniority=["director"],
        exclude_industries=["pharma"],
        include_keywords=["ai"],
    )
    jobs = [
        _job("Director AI", description="great ai role", is_remote=False),  # keep
        _job("Director AI", description="ai role", is_remote=True),         # drop: remote
        _job("Director AI", company="Pharma Co", description="ai", is_remote=False),  # drop: industry
        _job("Director Sales", description="no relevant words", is_remote=False),     # drop: include kw + seniority? has director but no 'ai'
        _job("Junior AI", description="ai role", is_remote=False),          # drop: seniority
    ]
    out_titles = titles(score_jobs(jobs, cfg))
    assert out_titles == ["Director AI"]
