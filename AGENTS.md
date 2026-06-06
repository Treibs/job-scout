# AGENTS.md — setting up job-scout for someone

You're an agent (e.g. Claude Code) asked to get **job-scout** running for a user.
It's a local, config-driven job-discovery + scoring engine, a two-pane CRM, and a
news board. This is the setup recipe. You can do most of it yourself — but **three
things only the user can provide**, so ask for those first.

## Ask the user first
1. **Their resume** — they drop a file or paste text; you save it to `resume/resume.md`
   (git-ignored). Scoring needs it. Without it, roles are still gathered + tracked, just
   unscored.
2. **What they're looking for** — titles, location, sectors, what to exclude. One sentence
   is enough ("AI leadership roles in Chicago, no healthcare"); draft `config/search.yaml`
   from it + their resume. With no steer, the example configs work as-is.
3. **An LLM for scoring** — either:
   - an **API key** (Anthropic, or any Anthropic-compatible endpoint such as MiniMax) →
     put it in `.env` as `ANTHROPIC_API_KEY` (+ optional `ANTHROPIC_BASE_URL`); **or**
   - **nothing** — if this machine has the **Claude Code CLI**, scoring auto-routes to it
     (no key, runs on their Claude subscription). Force it with
     `JOB_SCOUT_LLM_PROVIDER=claude_cli`. It's slower (a process per call) and uses
     subscription limits, so keep batches small — a daily scan only scores the handful of
     *new* roles (dedup skips the rest).

## Setup (you can do this)
```bash
python -m venv .venv && . .venv/bin/activate      # isolated venv — see footguns
pip install -r requirements.txt
for f in search companies scoring sources news; do cp config/$f.example.yaml config/$f.yaml; done
cp resume/resume.example.md resume/resume.md       # then replace with the user's real resume
cp .env.example .env                               # set a key, or rely on claude_cli auto-detect
```
Then personalize `config/search.yaml` (keywords, `target_sectors`, location) and
`config/companies.yaml` (target employers + ATS slugs) from the resume + the user's steer.

## Run
```bash
python scripts/run.py --config config/search.yaml   # scan → score → CSV tracker + dashboard
python scripts/news.py                               # optional: the relevant-news board
python scripts/serve.py                              # http://127.0.0.1:8765/  (Jobs + News)
```

## Footguns
- **Use an isolated venv** — `python-jobspy` hard-pins `numpy==1.26.3`; a fresh venv keeps it
  from fighting a system numpy.
- **`serve.py` is localhost-only** (binds 127.0.0.1) by design — don't expose it.
- **Personal data is git-ignored and must stay so**: `resume/`, `config/*.yaml` (but not
  `*.example.yaml`), `data/linkedin_connections.csv`, `output/`, `state/`, `.env`.
- **Optional extras**: drop a LinkedIn *connections* export at
  `data/linkedin_connections.csv` for the 🤝 "who you know" overlay; the news board needs no
  keys (free sources: Google News RSS + GDELT).
- **No auto-apply, ever** — the tool stops at discovery + scoring; a human applies.

## Verify
`pytest` should pass. After a scan, `output/jobs.csv` has rows and the dashboard serves at
`/`. Full reference: `README.md`; architecture: `PROJECT.md`.
