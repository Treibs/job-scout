#!/usr/bin/env python3
"""Generate the filterable HTML dashboard from the CSV tracker.

The daily pipeline already regenerates this after every run, but use this to
rebuild it on demand (e.g. after you hand-edit statuses in the CSV):

    python scripts/report.py                      # output/jobs.csv -> output/jobs.html
    python scripts/report.py --csv path.csv --out dash.html
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from job_scout.sinks import html_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="job-scout-report", description=__doc__)
    p.add_argument("--csv", default="output/jobs.csv", help="CSV tracker to read.")
    p.add_argument("--out", default=None, help="HTML output (default: <csv>.html).")
    args = p.parse_args(argv)

    out = html_report.render(args.csv, args.out)
    if out is None:
        print(f"No CSV at {args.csv!r} — run the pipeline first.", file=sys.stderr)
        return 1
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
