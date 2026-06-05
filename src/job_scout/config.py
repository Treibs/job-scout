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
    # Drop a job if its EMPLOYER matches any of these (substring, company field
    # only — so it won't false-drop a non-tech role that merely name-drops a
    # tech vendor in its description). Use to exclude Big Tech / AI labs.
    exclude_companies: list[str] = Field(default_factory=list)
    # Drop a job if its TITLE matches any of these (substring, title field only —
    # so a role that merely mentions e.g. "account executives" in its description
    # survives). Use to cut role types like sales/AE that aren't the target.
    exclude_title_keywords: list[str] = Field(default_factory=list)


class SearchCfg(BaseModel):
    keywords: list[str] = Field(default_factory=list)
    location: LocationCfg = Field(default_factory=LocationCfg)
    seniority: list[str] = Field(default_factory=list)
    freshness_hours: int = 72
    results_per_board: int = 30
    hard_filters: HardFilters = Field(default_factory=HardFilters)
    # The sectors / company types / role families the strategist should stay
    # within when proposing new keywords and companies. Personal to the user, so
    # it lives in config (git-ignored), not in code. Free text, e.g.
    # "banking/insurance, manufacturing — <metro> area, AI leadership roles".
    # Empty → the strategist falls back to a generic resume-derived guardrail.
    target_sectors: str = ""


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
    sites: list[str] = Field(default_factory=lambda: ["indeed", "linkedin"])
    proxies: list[str] = Field(default_factory=list)
    # LinkedIn only returns a description with a SECOND request per job. When on,
    # the enrich stage fetches JDs for only the top `linkedin_enrich_max` most
    # resume-relevant roles (cached, paced) — safe without proxies at that volume.
    linkedin_fetch_description: bool = False
    linkedin_enrich_max: int = 30


class AtsCfg(BaseModel):
    enabled: bool = True


class SourcesCfg(BaseModel):
    boards: BoardsCfg = Field(default_factory=BoardsCfg)
    ats: AtsCfg = Field(default_factory=AtsCfg)


# ── news.yaml ───────────────────────────────────────────────────────────────
class NewsSourcesCfg(BaseModel):
    google_news: bool = True
    gdelt: bool = True
    searxng: bool = False
    searxng_url: str = "http://127.0.0.1:8000"


class NewsCfg(BaseModel):
    enabled: bool = True
    # Role/domain trend phrases AND sector queries to search. Each becomes one
    # query per enabled source. If empty, falls back to terms from target_sectors.
    queries: list[str] = Field(default_factory=list)
    sources: NewsSourcesCfg = Field(default_factory=NewsSourcesCfg)
    freshness_hours: int = 96
    max_per_query: int = 20
    relevance_threshold: float = 0.6
    model: str = "MiniMax-M2.5-highspeed"


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
    news: NewsCfg = Field(default_factory=NewsCfg)
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

    cfg = Config(
        search=SearchCfg(**_read_yaml(search_p)),
        companies=CompaniesCfg(**_read_yaml(cfg_dir / "companies.yaml")),
        scoring=ScoringCfg(**_read_yaml(cfg_dir / "scoring.yaml")),
        sources=SourcesCfg(**_read_yaml(cfg_dir / "sources.yaml")),
        env=_load_env(),
        news=NewsCfg(**_read_yaml(cfg_dir / "news.yaml")),
        resume_text=resume_text,
        config_dir=str(cfg_dir),
    )
    _merge_discovery_additions(cfg, cfg_dir / "discovery_additions.yaml")
    return cfg


def _merge_discovery_additions(cfg: Config, path: Path) -> None:
    """Fold the strategist-managed ``discovery_additions.yaml`` into the config.

    Kept separate so the user's hand-curated (commented) search/companies YAML is
    never rewritten by the autonomous loop. Appends keywords, companies, and
    exclude_companies, de-duping against what's already there. Missing file = no-op.
    """
    add = _read_yaml(path)
    if not add:
        return

    existing_kw = {k.lower() for k in cfg.search.keywords}
    for kw in add.get("keywords") or []:
        if kw and kw.lower() not in existing_kw:
            cfg.search.keywords.append(kw)
            existing_kw.add(kw.lower())

    existing_co = {c.name.lower() for c in cfg.companies.companies}
    for co in add.get("companies") or []:
        try:
            target = CompanyTarget(**co)
        except Exception:  # noqa: BLE001 — skip malformed entries, never crash a run
            continue
        if target.name.lower() not in existing_co:
            cfg.companies.companies.append(target)
            existing_co.add(target.name.lower())

    hf = cfg.search.hard_filters
    existing_ex = {e.lower() for e in hf.exclude_companies}
    for ex in add.get("exclude_companies") or []:
        if ex and ex.lower() not in existing_ex:
            hf.exclude_companies.append(ex)
            existing_ex.add(ex.lower())
