#!/usr/bin/env python3
"""Run the news pull: gather free sources -> dedupe -> score -> cache, then render.

    python scripts/news.py                 # full run (scores if ANTHROPIC_API_KEY set)
    python scripts/news.py --no-render      # pull only, skip rendering the page

The cached, scored feed lands in state/news.json and renders to output/news.html
(served at /news by scripts/serve.py).
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from job_scout.config import load_config  # noqa: E402
from job_scout.news import pipeline  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="job-scout-news", description=__doc__)
    ap.add_argument("--config", default="config/search.yaml")
    ap.add_argument("--no-render", action="store_true", help="Pull only; don't render the page.")
    ap.add_argument("--enrich-missing", nargs="?", const=0, type=int, default=None, metavar="N",
                    help="Re-summarize cached items missing the 2-paragraph summary (optional limit N).")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.enrich_missing is not None:
        summary = pipeline.enrich_missing(cfg, limit=args.enrich_missing or None)
    else:
        summary = pipeline.run(cfg)
    print(json.dumps(summary, indent=2))

    if not args.no_render and (summary.get("enabled") or "enriched" in summary):
        from job_scout.sinks import news_report
        out = news_report.render()
        print(f"rendered {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
