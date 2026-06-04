"""The strategist — the "thought" in the feedback loop.

Every few days it reasons over the discovery ledger, the recent high-scoring
roles, Robert's interest signals, and his résumé, then proposes changes to the
search — new keywords and companies, and prunes of its own dead experiments.

THE GUARDRAIL (non-negotiable): nothing is added unless it clearly fits the
résumé and the established target sectors, each with a written ``fit_reason`` and
a ``relevance`` ≥ threshold. Bending to an adjacent sector/title is allowed only
with a strong justification. Everything is logged; nothing is silent.

Division of labour:
- Keywords are handled end-to-end here (proposed + auto-applied to the
  strategist-owned ``config/discovery_additions.yaml``).
- Companies are *proposed* here (name + reason); their ATS slug needs a web
  lookup, so the cron (Kitsune, with web tools) resolves and adds them.
- The user's hand-curated config is NEVER rewritten. Prunes only touch the
  strategist's own additions; suggestions about user-curated items go in the
  report for the human to act on.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path

import yaml

log = logging.getLogger("job_scout.strategist")

TARGET_SECTORS = (
    "banking/insurance, industrials/manufacturing, CPG/food/retail, "
    "sports/entertainment — Chicago area, AI/innovation/transformation leadership"
)
RELEVANCE_THRESHOLD = 0.7
_DEAD_AFTER_RUNS = 6  # an arm active this many runs with no high-scorer is "dead"


# ── digest ───────────────────────────────────────────────────────────────────
def digest(config, ledger: dict, csv_rows: list[dict], top_n: int = 15) -> dict:
    """Assemble everything the proposal step reasons over."""
    kw = ledger.get("keywords", {})
    co = ledger.get("companies", {})

    def _productive(d):
        return sorted(
            ((name, s) for name, s in d.items() if s.get("high", 0) > 0),
            key=lambda x: (x[1].get("high", 0), x[1].get("avg_score", 0)), reverse=True,
        )

    def _dead(d):
        return [name for name, s in d.items()
                if s.get("runs", 0) >= _DEAD_AFTER_RUNS and s.get("high", 0) == 0]

    scored = [r for r in csv_rows if _to_float(r.get("score")) is not None]
    scored.sort(key=lambda r: _to_float(r.get("score")) or -1, reverse=True)
    top = [{"title": r.get("title"), "company": r.get("company"),
            "score": r.get("score"), "status": r.get("status")} for r in scored[:top_n]]
    interest = [{"title": r.get("title"), "company": r.get("company"), "status": r.get("status")}
                for r in csv_rows if (r.get("status") or "") in ("interested", "applied")]

    return {
        "current_keywords": list(config.search.keywords),
        "current_companies": [c.name for c in config.companies.companies],
        "excluded_companies": list(config.search.hard_filters.exclude_companies),
        "productive_keywords": [{"keyword": n, **_summ(s)} for n, s in _productive(kw)],
        "productive_companies": [{"company": n, **_summ(s)} for n, s in _productive(co)],
        "dead_keywords": _dead(kw),
        "dead_companies": _dead(co),
        "top_recent_roles": top,
        "interest_hits": interest,
    }


def _summ(s: dict) -> dict:
    return {"runs": s.get("runs", 0), "roles": s.get("roles", 0),
            "high": s.get("high", 0), "avg_score": s.get("avg_score", 0.0),
            "max_score": s.get("max_score", 0.0)}


# ── propose (LLM, guarded) ───────────────────────────────────────────────────
def propose(digest_data: dict, resume_text: str, client, model: str,
            threshold: float = RELEVANCE_THRESHOLD) -> dict:
    """Ask the model for guarded changes. ``client`` is an anthropic.Anthropic
    (or any object with ``messages.create``) — injectable for tests. Returns the
    parsed proposal, with adds filtered to relevance ≥ threshold."""
    system = (
        "You tune a personal job search for ONE candidate. Propose ONLY changes "
        "that clearly fit the résumé and these target sectors: " + TARGET_SECTORS + ".\n"
        "GUARDRAIL: every added keyword/company needs a one-sentence `fit_reason` "
        "tying it to the résumé + existing targets, and a `relevance` from 0 to 1. "
        "Bending to an adjacent sector or title is allowed ONLY with a strong "
        "fit_reason. Favor companies similar to the productive ones and to where "
        "the candidate showed interest. Do NOT propose Big Tech, AI labs, major "
        "consulting, or anything already excluded. Be selective — a few strong "
        "additions beat many weak ones.\n"
        "Respond with STRICT JSON ONLY, no prose, no fences:\n"
        "{\n"
        '  "add_keywords": [{"keyword": str, "fit_reason": str, "relevance": number}],\n'
        '  "add_companies": [{"name": str, "sector": str, "fit_reason": str, "relevance": number}],\n'
        '  "remove_keywords": [str],   // only previously-added experiments worth dropping\n'
        '  "notes": str                // 1-3 sentences on your reasoning\n'
        "}"
    )
    user = (
        "=== RÉSUMÉ ===\n" + resume_text[:6000] + "\n\n"
        "=== CURRENT SEARCH + PERFORMANCE (JSON) ===\n"
        + json.dumps(digest_data, ensure_ascii=False)[:12000] +
        "\n\nPropose changes now as strict JSON."
    )
    resp = client.messages.create(
        model=model, max_tokens=4096, system=system,
        messages=[{"role": "user", "content": user}],
    )
    parsed = _extract_json(_text(resp)) or {}
    return _filter(parsed, threshold)


def _filter(parsed: dict, threshold: float) -> dict:
    def keep(items):
        out = []
        for it in items or []:
            try:
                if float(it.get("relevance", 0)) >= threshold:
                    out.append(it)
            except (TypeError, ValueError):
                continue
        return out

    return {
        "add_keywords": keep(parsed.get("add_keywords")),
        "add_companies": keep(parsed.get("add_companies")),
        "remove_keywords": [str(k) for k in (parsed.get("remove_keywords") or [])],
        "notes": str(parsed.get("notes") or ""),
    }


# ── apply (strategist-owned file only) ───────────────────────────────────────
def apply_changes(config_dir, *, add_keywords=None, remove_keywords=None,
                  add_companies=None, add_exclude=None, notes="") -> Path:
    """Merge changes into ``config/discovery_additions.yaml`` (the only file the
    strategist owns). ``add_companies`` must be fully ATS-resolved dicts. Returns
    the path written."""
    path = Path(config_dir) / "discovery_additions.yaml"
    data = {}
    if path.exists():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}

    kws = list(data.get("keywords") or [])
    have = {k.lower() for k in kws}
    for k in add_keywords or []:
        if k and k.lower() not in have:
            kws.append(k); have.add(k.lower())
    remove = {r.lower() for r in (remove_keywords or [])}
    kws = [k for k in kws if k.lower() not in remove]

    cos = list(data.get("companies") or [])
    have_co = {(c.get("name") or "").lower() for c in cos}
    for c in add_companies or []:
        if c.get("name") and c["name"].lower() not in have_co:
            cos.append(c); have_co.add(c["name"].lower())

    excl = list(data.get("exclude_companies") or [])
    have_ex = {e.lower() for e in excl}
    for e in add_exclude or []:
        if e and e.lower() not in have_ex:
            excl.append(e); have_ex.add(e.lower())

    out = {"updated": date.today().isoformat(), "notes": notes,
           "keywords": kws, "companies": cos, "exclude_companies": excl}
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(
        "# Machine-managed by the strategist — safe to delete to reset its additions.\n"
        + yaml.safe_dump(out, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


# ── helpers ──────────────────────────────────────────────────────────────────
def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _text(resp) -> str:
    return "".join(
        getattr(b, "text", "") for b in getattr(resp, "content", []) or []
        if getattr(b, "type", None) == "text" or getattr(b, "text", None)
    )


def _extract_json(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None
