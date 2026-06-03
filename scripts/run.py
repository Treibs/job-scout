#!/usr/bin/env python3
"""CLI entry point for job-scout.

Both the daily cron and on-demand runs call the *same* `run_pipeline()` — this
script is just the thin command-line wrapper around it (see PROJECT.md §2, §9).

Usage:
    python scripts/run.py --config config/search.yaml
    python scripts/run.py --config config/search.yaml --resume resume/resume.md -v
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

# The package lives under src/ — make it importable when run from the repo root.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from job_scout.config import load_config  # noqa: E402
from job_scout.pipeline import run_pipeline  # noqa: E402

log = logging.getLogger("job_scout.run")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="job-scout",
        description="Discover, score, and track job listings from a config file.",
    )
    parser.add_argument(
        "--config",
        default="config/search.yaml",
        help="Path to search.yaml (siblings companies/scoring/sources.yaml are "
        "inferred from the same directory). Default: config/search.yaml",
    )
    parser.add_argument(
        "--resume",
        default=None,
        help="Path to resume.md. Default: inferred as <config_dir>/../resume/resume.md.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser.parse_args(argv)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _print_summary(jobs: list) -> None:
    """Short human-readable summary to stdout (logs go to stderr)."""
    total = len(jobs)
    print(f"\nJob scout run complete: {total} job(s) gathered and written.")

    scored = [j for j in jobs if getattr(j, "score", None) is not None]
    if not scored:
        print("(No scores present — pipeline ran the deterministic stages only.)")
        return

    top = sorted(scored, key=lambda j: j.score, reverse=True)[:5]
    print(f"\nTop {len(top)} by score:")
    for rank, job in enumerate(top, start=1):
        title = getattr(job, "title", "?") or "?"
        company = getattr(job, "company", "?") or "?"
        print(f"  {rank}. {title} — {company} — {job.score:.0f}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)

    try:
        cfg = load_config(args.config, args.resume)
    except FileNotFoundError as e:
        print(f"FATAL: config file not found: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 — surface config errors clearly, don't traceback-dump
        print(f"FATAL: failed to load config from {args.config!r}: {e}", file=sys.stderr)
        return 2

    log.info("loaded config from %s", args.config)

    try:
        jobs = run_pipeline(cfg)
    except Exception as e:  # noqa: BLE001
        log.exception("pipeline failed")
        print(f"FATAL: pipeline failed: {e}", file=sys.stderr)
        return 1

    _print_summary(jobs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
