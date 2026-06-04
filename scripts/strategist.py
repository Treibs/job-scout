#!/usr/bin/env python3
"""Strategist CLI (adaptive-discovery Phase 3).

Default run: digest the ledger + recent roles + résumé, ask the model for GUARDED
changes, auto-apply the keyword changes (to the strategist-owned
config/discovery_additions.yaml), and print a JSON report. Company additions are
*proposed* but NOT applied here — they need an ATS lookup, which the Kitsune cron
does (with web tools) before calling `--add-companies`.

    python scripts/strategist.py                         # digest+propose+apply keywords
    python scripts/strategist.py --dry-run               # propose only, change nothing
    python scripts/strategist.py --add-companies '<json list of resolved entries>'
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from job_scout.config import load_config  # noqa: E402
from job_scout import ledger as ledger_mod  # noqa: E402
from job_scout import strategist as S  # noqa: E402


def _load_csv(path: str) -> list[dict]:
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="job-scout-strategist", description=__doc__)
    ap.add_argument("--config", default="config/search.yaml")
    ap.add_argument("--csv", default="output/jobs.csv")
    ap.add_argument("--dry-run", action="store_true", help="Propose only; apply nothing.")
    ap.add_argument("--model", default=os.getenv("JOB_SCOUT_STRATEGIST_MODEL", "MiniMax-M2.5-highspeed"))
    ap.add_argument("--add-companies", default=None,
                    help="JSON list of ATS-resolved company entries to append (used by the cron).")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    cfg_dir = pathlib.Path(args.config).parent

    # Cron path: append already-resolved companies and exit.
    if args.add_companies is not None:
        companies = json.loads(args.add_companies)
        path = S.apply_changes(cfg_dir, add_companies=companies, notes="companies added by strategist cron")
        print(json.dumps({"added_companies": len(companies), "file": str(path)}))
        return 0

    dg = S.digest(cfg, ledger_mod.load(), _load_csv(args.csv))

    key = cfg.env.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
    if not key:
        print(json.dumps({"digest": dg, "note": "no ANTHROPIC_API_KEY — digest only"}, indent=2))
        return 0

    import anthropic  # lazy

    client = anthropic.Anthropic(api_key=key)
    proposal = S.propose(dg, cfg.resume_text, client, args.model)

    if not args.dry_run and (proposal["add_keywords"] or proposal["remove_keywords"]):
        S.apply_changes(
            cfg_dir,
            add_keywords=[k["keyword"] for k in proposal["add_keywords"]],
            remove_keywords=proposal["remove_keywords"],
            notes=proposal["notes"],
        )

    print(json.dumps({
        "applied": not args.dry_run,
        "keywords_added": [k["keyword"] for k in proposal["add_keywords"]],
        "keywords_removed": proposal["remove_keywords"],
        "companies_proposed": proposal["add_companies"],  # cron resolves ATS + adds
        "notes": proposal["notes"],
        "digest": dg,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
