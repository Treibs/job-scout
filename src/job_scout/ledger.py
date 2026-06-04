"""Discovery ledger — the memory the strategist learns from.

After each run we record, per keyword and per company: how many runs it's been
active, how many roles it has surfaced over time, how many scored well (≥60), the
running average/max score, and (for companies) how many you've flagged
interested/applied. The strategist reads this to decide what's productive, what's
dead, and where to expand.

Written to ``state/discovery.json`` (gitignored — personal). Never raises; a bad
ledger must not break a run.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from .models import Job

log = logging.getLogger("job_scout.ledger")

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LEDGER_PATH = _REPO_ROOT / "state" / "discovery.json"

_HIGH = 60.0  # score at/above which a role counts as a "high" hit


def _load() -> dict:
    try:
        data = json.loads(_LEDGER_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load() -> dict:
    """Public read of the current ledger (used by the strategist)."""
    return _load()


def _save(data: dict) -> None:
    try:
        _LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LEDGER_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(_LEDGER_PATH)
    except OSError as e:
        log.warning("ledger save failed: %s", e)


def _blank(extra: dict | None = None) -> dict:
    base = {"runs": 0, "roles": 0, "high": 0, "score_sum": 0.0,
            "avg_score": 0.0, "max_score": 0.0, "last_productive": None}
    if extra:
        base.update(extra)
    return base


def _bump_arm(arm: dict, score: float | None, today: str) -> None:
    arm["roles"] += 1
    if score is not None:
        arm["score_sum"] += score
        arm["max_score"] = max(arm.get("max_score", 0.0), score)
        if score >= _HIGH:
            arm["high"] += 1
            arm["last_productive"] = today
    if arm["roles"]:
        arm["avg_score"] = round(arm["score_sum"] / arm["roles"], 1)


def record(jobs: list[Job], config, interest_by_company: dict | None = None,
           today: str | None = None) -> dict:
    """Fold this run's scored ``jobs`` into the ledger and persist it.

    ``interest_by_company`` (company -> {"interested": n, "applied": n}) is a
    snapshot of the current tracker state; pass it from the pipeline. Returns the
    updated ledger dict.
    """
    today = today or date.today().isoformat()
    led = _load()
    led["runs"] = int(led.get("runs", 0)) + 1
    led["updated"] = today
    kw = led.setdefault("keywords", {})
    co = led.setdefault("companies", {})

    # Every active arm gets a run tick (so yield = roles/runs; dead arms accrue
    # runs with no roles and surface as prunable).
    for k in config.search.keywords:
        kw.setdefault(k, _blank())["runs"] += 1
    for c in config.companies.companies:
        co.setdefault(c.name, _blank({"interested": 0, "applied": 0}))["runs"] += 1

    for job in jobs:
        if job.search_term and job.search_term in kw:
            _bump_arm(kw[job.search_term], job.score, today)
        if job.company in co:
            _bump_arm(co[job.company], job.score, today)

    if interest_by_company:
        for name, counts in interest_by_company.items():
            entry = co.setdefault(name, _blank({"interested": 0, "applied": 0}))
            entry["interested"] = int(counts.get("interested", 0))
            entry["applied"] = int(counts.get("applied", 0))

    _save(led)
    log.info("ledger: run #%d recorded (%d keywords, %d companies tracked)",
             led["runs"], len(kw), len(co))
    return led
