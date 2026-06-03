"""Tests for config loading + validation (config.py).

Strategy: write the four committed *.example.yaml bodies into a temp config dir
as the real *.yaml names, then point load_config at search.yaml. Asserts the
pydantic models validate types (keywords list, scoring dimensions, companies
parse incl. workday-specific keys) and that env vars load (missing -> None).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pydantic")
pytest.importorskip("yaml")

from job_scout.config import load_config, Config


# Repo config/ holding the committed *.example.yaml templates.
_REPO_CONFIG = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    """A temp config/ dir populated from the committed *.example.yaml files,
    plus a sibling resume/ dir, mirroring the real repo layout."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    for name in ("search", "companies", "scoring", "sources"):
        example = _REPO_CONFIG / f"{name}.example.yaml"
        (cfg / f"{name}.yaml").write_text(example.read_text())

    resume_dir = tmp_path / "resume"
    resume_dir.mkdir()
    (resume_dir / "resume.md").write_text("# Jane Doe\nAI leadership experience.\n")

    # Ensure env vars are unset so we test the missing -> None path by default.
    for var in ("ANTHROPIC_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID", "PROXY_URLS"):
        monkeypatch.delenv(var, raising=False)

    return cfg


def test_load_config_returns_config(config_dir):
    cfg = load_config(config_dir / "search.yaml")
    assert isinstance(cfg, Config)


def test_search_cfg_types(config_dir):
    cfg = load_config(config_dir / "search.yaml")
    assert isinstance(cfg.search.keywords, list)
    assert "Director AI" in cfg.search.keywords
    assert cfg.search.location.query == "Chicago, IL"
    assert cfg.search.location.distance_miles == 50
    assert cfg.search.location.remote_policy == "include"
    assert cfg.search.freshness_hours == 72
    assert cfg.search.results_per_board == 30
    assert isinstance(cfg.search.seniority, list)
    assert "director" in cfg.search.seniority


def test_hard_filters_parse(config_dir):
    cfg = load_config(config_dir / "search.yaml")
    hf = cfg.search.hard_filters
    assert "healthcare" in hf.exclude_industries
    assert isinstance(hf.exclude_keywords, list)
    assert isinstance(hf.include_keywords, list)


def test_scoring_dimensions_parse(config_dir):
    cfg = load_config(config_dir / "search.yaml")
    dims = cfg.scoring.dimensions
    assert len(dims) == 5
    ids = [d.id for d in dims]
    assert "mission_impact" in ids
    # weights are numeric and prompts are non-empty strings.
    for d in dims:
        assert isinstance(d.weight, (int, float))
        assert isinstance(d.prompt, str) and d.prompt
    assert cfg.scoring.scale == [0, 5]
    assert cfg.scoring.role_fit_gate is True
    assert cfg.scoring.model  # configurable model name present
    assert cfg.scoring.pre_filter.enabled is True
    assert cfg.scoring.pre_filter.method == "embedding"
    assert cfg.scoring.pre_filter.threshold == pytest.approx(0.75)


def test_companies_parse_including_workday_fields(config_dir):
    cfg = load_config(config_dir / "search.yaml")
    companies = cfg.companies.companies
    assert len(companies) >= 2
    by_ats = {c.ats: c for c in companies}
    assert "greenhouse" in by_ats
    gh = by_ats["greenhouse"]
    assert gh.slug  # greenhouse uses slug
    # workday-specific fields parse onto the model.
    wd = by_ats.get("workday")
    if wd is not None:
        assert wd.tenant == "examplebank"
        assert wd.site == "External"
        assert wd.datacenter == "wd1"


def test_sources_parse(config_dir):
    cfg = load_config(config_dir / "search.yaml")
    assert cfg.sources.boards.enabled is True
    assert "indeed" in cfg.sources.boards.sites
    assert cfg.sources.ats.enabled is True


def test_env_missing_vars_are_none(config_dir):
    cfg = load_config(config_dir / "search.yaml")
    assert cfg.env.anthropic_api_key is None
    assert cfg.env.google_service_account_json is None
    assert cfg.env.sheet_id is None
    assert cfg.env.proxy_urls == []


def test_env_vars_load_when_present(config_dir, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    monkeypatch.setenv("SHEET_ID", "sheet-abc")
    monkeypatch.setenv("PROXY_URLS", "user:pass@host:8080, host2:9090")
    cfg = load_config(config_dir / "search.yaml")
    assert cfg.env.anthropic_api_key == "sk-test-123"
    assert cfg.env.sheet_id == "sheet-abc"
    # comma-split, stripped, empties dropped.
    assert cfg.env.proxy_urls == ["user:pass@host:8080", "host2:9090"]


def test_resume_text_loaded_from_sibling(config_dir):
    cfg = load_config(config_dir / "search.yaml")
    assert "Jane Doe" in cfg.resume_text


def test_missing_resume_yields_empty_string(tmp_path, monkeypatch):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "search.yaml").write_text("keywords: []\n")
    for var in ("ANTHROPIC_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID", "PROXY_URLS"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(cfg_dir / "search.yaml")
    assert cfg.resume_text == ""


def test_missing_sibling_yamls_use_defaults(tmp_path, monkeypatch):
    # Only search.yaml present; siblings absent -> models fall back to defaults.
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "search.yaml").write_text("keywords:\n  - X\n")
    for var in ("ANTHROPIC_API_KEY", "GOOGLE_SERVICE_ACCOUNT_JSON", "SHEET_ID", "PROXY_URLS"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(cfg_dir / "search.yaml")
    assert cfg.search.keywords == ["X"]
    assert cfg.companies.companies == []
    assert cfg.scoring.dimensions == []
    assert cfg.sources.boards.enabled is True  # default
