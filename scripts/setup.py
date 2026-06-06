#!/usr/bin/env python3
"""job-scout front door — idempotent setup + run.

Built to be driven by an agent (Claude Code) *or* a human. It's flag-driven (no
interactive prompts), so an agent gathers answers in chat then calls it. It never
overwrites your config, resume, tracker, or state — re-running just **resumes**.

  Fresh machine:
    python scripts/setup.py --resume ~/resume.md [--linkedin ~/Connections.csv]
                            [--provider claude_cli] [--no-news]
  Returning (data already present):
    python scripts/setup.py        # detects your tracker, summarizes, refreshes

What it does: scaffold any missing config/*.yaml + resume + .env from templates,
place your resume / LinkedIn export, run the scan (+ news), then print the serve
command and a ready-to-paste daily cron (install only with --install-cron).
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path
from shutil import copyfile

ROOT = Path(__file__).resolve().parents[1]
_CONFIGS = ["search", "companies", "scoring", "sources", "news"]


def scaffold(root: Path, resume_src=None, linkedin_src=None) -> list[str]:
    """Create any MISSING config/resume/.env from templates (never overwrites).
    Returns the list of files created/placed."""
    made: list[str] = []
    cfg = root / "config"
    for name in _CONFIGS:
        dst, src = cfg / f"{name}.yaml", cfg / f"{name}.example.yaml"
        if not dst.exists() and src.exists():
            copyfile(src, dst)
            made.append(f"config/{name}.yaml")
    if not (root / ".env").exists() and (root / ".env.example").exists():
        copyfile(root / ".env.example", root / ".env")
        made.append(".env")

    resume = root / "resume" / "resume.md"
    if resume_src:
        resume.parent.mkdir(exist_ok=True)
        copyfile(resume_src, resume)
        made.append("resume/resume.md (your resume)")
    elif not resume.exists() and (root / "resume" / "resume.example.md").exists():
        copyfile(root / "resume" / "resume.example.md", resume)
        made.append("resume/resume.md (TEMPLATE — replace with yours)")

    if linkedin_src:
        (root / "data").mkdir(exist_ok=True)
        copyfile(linkedin_src, root / "data" / "linkedin_connections.csv")
        made.append("data/linkedin_connections.csv (your connections)")
    return made


def tracker_summary(csv_path: Path) -> dict | None:
    """{total, by_status} for an existing tracker, or None if fresh/empty."""
    if not csv_path.exists():
        return None
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return None
    if not rows:
        return None
    return {"total": len(rows),
            "by_status": dict(Counter((r.get("status") or "new") for r in rows))}


def cron_lines(root: Path, python: str, hour: int = 7) -> list[str]:
    """The three recurring jobs as crontab lines (daily scan, daily news, 3-day strategist)."""
    pre, cfg, log = f"cd {root} &&", "config/search.yaml", ">> state/cron.log 2>&1"
    return [
        f"0 {hour} * * *   {pre} {python} scripts/run.py --config {cfg} {log}",
        f"30 {hour} * * *  {pre} {python} scripts/news.py --config {cfg} {log}",
        f"0 {hour} */3 * * {pre} {python} scripts/strategist.py --config {cfg} {log}",
    ]


def _install_cron(lines: list[str]) -> None:
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except (FileNotFoundError, OSError):
        print("   ⚠ no crontab on this OS — add the lines above to your scheduler manually.")
        return
    fresh = [ln for ln in lines if ln not in existing]
    if not fresh:
        print("   cron already installed ✓")
        return
    body = (existing.rstrip("\n") + "\n" if existing.strip() else "") + "\n".join(fresh) + "\n"
    subprocess.run(["crontab", "-"], input=body, text=True)
    print(f"   installed {len(fresh)} cron line(s) ✓")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="job-scout-setup", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", help="path to your resume (.md/.txt) → resume/resume.md")
    ap.add_argument("--linkedin", help="path to a LinkedIn Connections.csv export → data/")
    ap.add_argument("--provider", choices=["anthropic", "claude_cli"], help="force the scoring provider")
    ap.add_argument("--config", default="config/search.yaml")
    ap.add_argument("--no-pull", action="store_true", help="scaffold only; don't run the scan")
    ap.add_argument("--no-news", action="store_true", help="skip the news pull")
    ap.add_argument("--install-cron", action="store_true", help="add the daily routine to your crontab")
    ap.add_argument("--hour", type=int, default=7, help="local hour for the daily cron (default 7)")
    args = ap.parse_args(argv)
    py = sys.executable

    # 1 · returning vs fresh
    summ = tracker_summary(ROOT / "output" / "jobs.csv")
    if summ:
        print("→ Returning setup — found your tracker:")
        print(f"   {summ['total']} roles · {summ['by_status']}")
        print("   (re-running resumes — upsert keeps your statuses/notes, dedup skips seen roles)")
    else:
        print("→ Fresh setup.")

    # 2 · scaffold (idempotent)
    made = scaffold(ROOT, args.resume, args.linkedin)
    for m in made:
        print(f"   + {m}")
    if not made:
        print("   config already in place — nothing to scaffold")

    # 3 · provider report (loud if none)
    if args.provider:
        os.environ["JOB_SCOUT_LLM_PROVIDER"] = args.provider
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from job_scout import llm
        from job_scout.config import load_config
        cfg = load_config(str(ROOT / args.config))
        provider = llm.resolve_provider(cfg, args.provider)
        ok = llm.available(provider, cfg)
        print(f"→ Scoring provider: {provider}" + (" (no API key needed)" if provider == "claude_cli" and ok else ""))
        if not ok:
            print("   ⚠ no LLM available — the scan will gather + track but NOT score.")
            print("     Set ANTHROPIC_API_KEY in .env, or install Claude Code, then re-run.")
    except Exception as e:  # noqa: BLE001
        print(f"   ⚠ could not load config ({e}) — run `pip install -r requirements.txt` first?")
        return 2

    # 4 · the scan
    if args.no_pull:
        print("→ Skipping the scan (--no-pull). Personalize config/search.yaml, then re-run.")
    else:
        print("→ Running the initial scan (this can take a few minutes)…")
        subprocess.run([py, "scripts/run.py", "--config", args.config], cwd=ROOT, env=os.environ)
        if not args.no_news:
            print("→ Refreshing the news board…")
            subprocess.run([py, "scripts/news.py", "--config", args.config], cwd=ROOT, env=os.environ)
        post = tracker_summary(ROOT / "output" / "jobs.csv")
        if post:
            print(f"   tracker now holds {post['total']} roles.")
        else:
            print("   ⚠ no roles written — check your config/search.yaml criteria and provider above.")

    # 5 · next steps + the daily routine
    print("\nNext:")
    print(f"   {py} scripts/serve.py     →  http://127.0.0.1:8765/   (Jobs + News dashboards)")
    print("   Daily routine (the feedback loop) — add to your crontab (`crontab -e`):")
    lines = cron_lines(ROOT, py, args.hour)
    for ln in lines:
        print(f"     {ln}")
    if args.install_cron:
        _install_cron(lines)
    else:
        print("   …or re-run with --install-cron to add them for you. (Hosted? see .github/workflows/.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
