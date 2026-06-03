"""Load + validate configuration. All personalization lives in config files and
env vars — never in code (design principle #1).

`load_config("config/search.yaml")` reads that file plus its siblings
(`companies.yaml`, `scoring.yaml`, `sources.yaml`) from the same directory, then
folds in env vars and the resume text. Pass the resulting `Config` everywhere.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

try:  # optional in CI (env already set); convenient in local dev
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass


# ── search.yaml ─────────────────────────────────────────────────────────────
class LocationCfg(BaseModel):
    query: str = ""
    distance_miles: int = 50
    remote_policy: Literal["include", "exclude", "only"] = "include"


class HardFilters(BaseModel):
    exclude_industries: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    include_keywords: list[str] = Field(default_factory=list)


class SearchCfg(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    location: LocationCfg = Field(default_factory=LocationCfg)
    seniority: list[str] = Field(default_factory=list)
    freshness_hours: int = 72
    results_per_board: int = 30
    hard_filters: HardFilters = Field(default_factory=HardFilters)


# ── companies.yaml ──────────────────────────────────────────────────────────
class CompanyTarget(BaseModel):
    name: str
    ats: Literal["greenhouse", "lever", "ashby", "smartrecruiters", "workday"]
    slug: str | None = None  # greenhouse/lever/ashby/smartrecruiters
    # workday-specific:
    tenant: str | None = None
    site: str | None = None
    datacenter: str | None = None


class CompaniesCfg(BaseModel):
    companies: list[CompanyTarget] = Field(default_factory=list)


# ── scoring.yaml ────────────────────────────────────────────────────────────
class Dimension(BaseModel):
    id: str
    weight: float
    prompt: str


class PreFilterCfg(BaseModel):
    enabled: bool = True
    method: Literal["embedding", "keyword", "none"] = "embedding"
    threshold: float = 0.75


class ScoringCfg(BaseModel):
    dimensions: list[Dimension] = Field(default_factory=list)
    scale: list[int] = Field(default_factory=lambda: [0, 5])
    role_fit_gate: bool = True
    model: str = "claude-sonnet-4-5"
    pre_filter: PreFilterCfg = Field(default_factory=PreFilterCfg)


# ── sources.yaml ────────────────────────────────────────────────────────────
class BoardsCfg(BaseModel):
    enabled: bool = True
    sites: list[str] = Field(default_factory=lambda: ["indeed", "google"])
    proxies: list[str] = Field(default_factory=list)


class AtsCfg(BaseModel):
    enabled: bool = True


class SourcesCfg(BaseModel):
    boards: BoardsCfg = Field(default_factory=BoardsCfg)
    ats: AtsCfg = Field(default_factory=AtsCfg)


# ── env ─────────────────────────────────────────────────────────────────────
class EnvCfg(BaseModel):
    anthropic_api_key: str | None = None
    google_service_account_json: str | None = None  # raw JSON or a path
    sheet_id: str | None = None
    proxy_urls: list[str] = Field(default_factory=list)
    # Which tracker sink to write. "csv" needs no external service or creds.
    sink: Literal["csv", "google_sheets"] = "csv"
    jobs_csv_path: str | None = None  # csv sink output (default: output/jobs.csv)


# ── aggregate ───────────────────────────────────────────────────────────────
class Config(BaseModel):
    search: SearchCfg
    companies: CompaniesCfg
    scoring: ScoringCfg
    sources: SourcesCfg
    env: EnvCfg
    resume_text: str = ""
    config_dir: str = "config"


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text()) or {}


def _load_env() -> EnvCfg:
    proxies = [p.strip() for p in (os.getenv("PROXY_URLS") or "").split(",") if p.strip()]
    return EnvCfg(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        google_service_account_json=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"),
        sheet_id=os.getenv("SHEET_ID"),
        proxy_urls=proxies,
        sink=(os.getenv("JOB_SCOUT_SINK") or "csv").strip().lower(),
        jobs_csv_path=os.getenv("JOBS_CSV_PATH"),
    )


def load_config(search_path: str | Path, resume_path: str | Path | None = None) -> Config:
    """Load the full config given a path to search.yaml (siblings inferred)."""
    search_p = Path(search_path)
    cfg_dir = search_p.parent

    resume_p = Path(resume_path) if resume_path else cfg_dir.parent / "resume" / "resume.md"
    resume_text = resume_p.read_text() if Path(resume_p).exists() else ""

    return Config(
        search=SearchCfg(**_read_yaml(search_p)),
        companies=CompaniesCfg(**_read_yaml(cfg_dir / "companies.yaml")),
        scoring=ScoringCfg(**_read_yaml(cfg_dir / "scoring.yaml")),
        sources=SourcesCfg(**_read_yaml(cfg_dir / "sources.yaml")),
        env=_load_env(),
        resume_text=resume_text,
        config_dir=str(cfg_dir),
    )
