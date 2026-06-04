"""Backend tests for the adaptive-discovery loop: config merge, ledger, strategist."""

from __future__ import annotations

import json

import yaml

from job_scout import ledger, strategist
from job_scout.models import Job
from job_scout.config import (
    load_config, Config, SearchCfg, HardFilters, CompaniesCfg, CompanyTarget,
    ScoringCfg, SourcesCfg, EnvCfg,
)


def _cfg(keywords=None, companies=None, exclude=None):
    return Config(
        search=SearchCfg(keywords=keywords or [],
                         hard_filters=HardFilters(exclude_companies=exclude or [])),
        companies=CompaniesCfg(companies=companies or []),
        scoring=ScoringCfg(), sources=SourcesCfg(), env=EnvCfg(), resume_text="",
    )


# ── config merge of discovery_additions.yaml ─────────────────────────────────
def _write_cfg_dir(tmp_path):
    (tmp_path / "search.yaml").write_text(yaml.safe_dump({"keywords": ["AI Strategy"]}))
    (tmp_path / "companies.yaml").write_text(yaml.safe_dump(
        {"companies": [{"name": "Caterpillar", "ats": "greenhouse", "slug": "cat"}]}))
    return tmp_path


def test_merge_additions_appends_and_dedups(tmp_path):
    _write_cfg_dir(tmp_path)
    (tmp_path / "discovery_additions.yaml").write_text(yaml.safe_dump({
        "keywords": ["AI Strategy", "Chief AI Officer"],  # first is a dup
        "companies": [{"name": "U.S. Bank", "ats": "workday", "tenant": "usbank",
                       "site": "careers", "datacenter": "wd1"}],
        "exclude_companies": ["optum"],
    }))
    cfg = load_config(tmp_path / "search.yaml", tmp_path / "none.md")
    assert cfg.search.keywords == ["AI Strategy", "Chief AI Officer"]  # dup collapsed
    assert {c.name for c in cfg.companies.companies} == {"Caterpillar", "U.S. Bank"}
    assert "optum" in cfg.search.hard_filters.exclude_companies


def test_merge_missing_file_is_noop(tmp_path):
    _write_cfg_dir(tmp_path)
    cfg = load_config(tmp_path / "search.yaml", tmp_path / "none.md")
    assert cfg.search.keywords == ["AI Strategy"]


# ── ledger ───────────────────────────────────────────────────────────────────
def test_ledger_records_yield_and_interest(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger, "_LEDGER_PATH", tmp_path / "discovery.json")
    cfg = _cfg(keywords=["AI Strategy"],
               companies=[CompanyTarget(name="Caterpillar", ats="greenhouse", slug="cat")])
    jobs = [
        Job(id="1", title="Director AI", company="Caterpillar", url="u1",
            source="linkedin", search_term="AI Strategy", score=72.0),
        Job(id="2", title="Mgr AI", company="Caterpillar", url="u2",
            source="indeed", search_term="AI Strategy", score=40.0),
    ]
    led = ledger.record(jobs, cfg, interest_by_company={"Caterpillar": {"interested": 2, "applied": 1}})
    assert led["runs"] == 1
    kw = led["keywords"]["AI Strategy"]
    assert kw["roles"] == 2 and kw["high"] == 1 and kw["max_score"] == 72.0
    co = led["companies"]["Caterpillar"]
    assert co["roles"] == 2 and co["high"] == 1
    assert co["interested"] == 2 and co["applied"] == 1
    # second run accumulates
    led = ledger.record(jobs, cfg)
    assert led["runs"] == 2 and led["keywords"]["AI Strategy"]["roles"] == 4


# ── strategist ───────────────────────────────────────────────────────────────
def test_strategist_apply_writes_additions(tmp_path):
    strategist.apply_changes(
        tmp_path,
        add_keywords=["Chief AI Officer", "Head of Innovation"],
        add_companies=[{"name": "U.S. Bank", "ats": "workday", "tenant": "usbank",
                        "site": "careers", "datacenter": "wd1"}],
        add_exclude=["optum"], notes="test run",
    )
    data = yaml.safe_load((tmp_path / "discovery_additions.yaml").read_text())
    assert "Chief AI Officer" in data["keywords"]
    assert data["companies"][0]["name"] == "U.S. Bank"
    assert "optum" in data["exclude_companies"]
    # re-apply dedups and can prune its own keyword
    strategist.apply_changes(tmp_path, add_keywords=["Chief AI Officer"],
                             remove_keywords=["Head of Innovation"])
    data = yaml.safe_load((tmp_path / "discovery_additions.yaml").read_text())
    assert data["keywords"] == ["Chief AI Officer"]


def test_strategist_propose_filters_by_relevance():
    class FakeResp:
        content = [type("B", (), {"type": "text", "text": json.dumps({
            "add_keywords": [
                {"keyword": "Chief AI Officer", "fit_reason": "fits exec AI goal", "relevance": 0.9},
                {"keyword": "Barista", "fit_reason": "weak", "relevance": 0.2},
            ],
            "add_companies": [{"name": "U.S. Bank", "sector": "banking",
                               "fit_reason": "Chicago bank, AI leadership", "relevance": 0.85}],
            "remove_keywords": ["Dead Keyword"],
            "notes": "leaning into banking + exec AI titles",
        })})()]
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kw): return FakeResp()
    out = strategist.propose({"current_keywords": []}, "résumé text",
                             FakeClient(), "MiniMax-M2.5-highspeed")
    kws = [k["keyword"] for k in out["add_keywords"]]
    assert kws == ["Chief AI Officer"]          # low-relevance "Barista" dropped
    assert out["add_companies"][0]["name"] == "U.S. Bank"
    assert out["remove_keywords"] == ["Dead Keyword"]


def test_strategist_guard_drops_tracked_and_protects_core():
    """The guard must not re-add tracked items, and must never remove core keywords."""
    digest = {
        "current_keywords": ["AI Strategy"],
        "current_companies": ["Caterpillar"],
        "excluded_companies": ["google"],
    }
    raw = {
        "add_keywords": [{"keyword": "AI Strategy", "relevance": 0.9},   # already tracked
                         {"keyword": "AI Governance", "relevance": 0.9}],  # new
        "add_companies": [{"name": "Caterpillar", "relevance": 0.9},      # already tracked
                          {"name": "Google", "relevance": 0.9},           # excluded
                          {"name": "U.S. Bank", "relevance": 0.9}],       # new
        "remove_keywords": ["AI Strategy", "Old Experiment"],            # 1st is core
        "notes": "",
    }
    out = strategist._guard(strategist._filter(raw, 0.7), digest)
    assert [k["keyword"] for k in out["add_keywords"]] == ["AI Governance"]
    assert [c["name"] for c in out["add_companies"]] == ["U.S. Bank"]
    assert out["remove_keywords"] == ["Old Experiment"]  # core "AI Strategy" protected
