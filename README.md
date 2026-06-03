# job-scout

An open-source, config-driven job **discovery and scoring** engine. It scrapes
public job listings from multiple sources, cross-references them against *your*
resume, scores fit against *your* weighted criteria, and writes a ranked,
deduplicated tracker to Google Sheets — on a daily schedule and on demand.

> **It is a discovery + scoring + tracker. It is *not* an auto-applier.**
> job-scout never logs into a site, fills a form, or submits an application. It
> finds relevant roles, ranks them by your priorities, and hands you a clean
> tracker. A human makes the final apply decision, every time. This is a
> deliberate design choice — see [Ethics & ToS](#ethics--tos) below.

There is **no personal data in this repo.** Everything user-specific lives in
git-ignored config and resume files; the repo ships only `*.example` templates.

---

## How it works

One pipeline, two triggers. The daily cron and the on-demand run both call the
same `run_pipeline(config)` — no duplicated logic.

```
                    ┌──────────────────────────────────────────────┐
   TRIGGERS         │                 run_pipeline(config)          │
 ┌───────────┐      │                                               │
 │ cron      │─────►│  sources ─► normalize ─► dedupe ─► score ─► sink
 │ (daily)   │      │   │                                      │      │
 ├───────────┤      │   ├─ jobspy (boards)        ┌────────────┘      │
 │ dispatch  │─────►│   └─ ats/* (greenhouse,     │ embedding pre-filter│
 │ (on-demand)│     │       lever, ashby,         │ + LLM rubric scoring│
 └───────────┘      │       smartrecruiters,      └─────────────────────┘
                    │       workday)                       │
                    │                                      ▼
                    │   state/ (seen hashes, snapshot)   Google Sheets
                    └──────────────────────────────────────────────┘
```

**Pipeline stages:**

1. **sources** — each source returns raw job dicts. [JobSpy](https://github.com/cullenwatson/JobSpy)
   pulls public boards (Indeed, Google, optionally LinkedIn); one module per ATS
   pulls official public JSON (Greenhouse, Lever, Ashby, SmartRecruiters,
   Workday). Each source is wrapped so one dead source can't kill the run.
2. **normalize** — every source is mapped into one `Job` schema.
3. **dedupe** — composite-key exact match (`title + company + location`,
   normalized) plus a fuzzy `rapidfuzz` second pass, across sources *and* across
   runs. Prefers the direct ATS/apply URL over the aggregator link.
4. **score** — cheap deterministic hard filters, then an optional embedding
   pre-filter (`sentence-transformers`), then LLM rubric scoring on the
   survivors. Ranked by overall score.
5. **sinks** — upserts the ranked tracker into Google Sheets and writes
   `state/` (seen hashes + snapshot), which the cron commits back to the repo.

### Configuration model

All personalization lives in `config/` and `resume/`. Ship only the `.example`
files; copy them to the real (git-ignored) names and edit.

| File | Purpose |
|------|---------|
| [`config/search.yaml`](config/search.example.yaml) | What/where to search: `keywords`, `location` (`query`, `distance_miles`, `remote_policy`), `seniority`, `freshness_hours`, `results_per_board`, and `hard_filters` (excluded industries/keywords, include keywords). |
| [`config/companies.yaml`](config/companies.example.yaml) | Target employers for the direct-ATS pulls: `name`, `ats`, and the slug/tenant fields. See [docs/finding-ats-slugs.md](docs/finding-ats-slugs.md). |
| [`config/scoring.yaml`](config/scoring.example.yaml) | Your rubric `dimensions` (each with `id`, `weight`, `prompt`), `scale`, `role_fit_gate`, the scoring `model`, and the embedding `pre_filter`. No opinionated defaults are baked into code. |
| [`config/sources.yaml`](config/sources.example.yaml) | Which sources are on/off: `boards` (JobSpy `sites`, optional `proxies`) and `ats` (uses `companies.yaml`). |
| [`resume/resume.md`](resume/resume.example.md) | Plain-markdown resume the scorer compares each listing against. Git-ignored. |

---

## Quickstart

A stranger should be able to clone this, add a resume + a service account, and
get a working daily tracker by following the checklist below. (Mirrors
PROJECT.md §13.)

- [ ] **Clone and install**

  ```bash
  git clone https://github.com/OWNER/REPO.git job-scout
  cd job-scout
  pip install -r requirements.txt
  ```

- [ ] **Copy each config template** `config/*.example.yaml` → `config/*.yaml`
      and edit. (The real `*.yaml` names are git-ignored.)

  ```bash
  cp config/search.example.yaml    config/search.yaml
  cp config/companies.example.yaml config/companies.yaml
  cp config/scoring.example.yaml   config/scoring.yaml
  cp config/sources.example.yaml   config/sources.yaml
  ```

- [ ] **Copy your resume** `resume/resume.example.md` → `resume/resume.md` and
      paste in your real resume.

  ```bash
  cp resume/resume.example.md resume/resume.md
  ```

- [ ] **Fill `config/companies.yaml`** with your target employers + ATS slugs.
      Don't know a company's ATS or slug? See
      [docs/finding-ats-slugs.md](docs/finding-ats-slugs.md).

- [ ] **Set up Google Sheets + the service account.** Create a GCP project,
      enable the Sheets API, create a service account, share your tracker Sheet
      with its email as Editor. Full walkthrough:
      [docs/sheets-setup.md](docs/sheets-setup.md).

- [ ] **Add secrets.** For local dev, copy `.env.example` → `.env` and fill it.
      For GitHub Actions, add these repository secrets
      (**Settings → Secrets and variables → Actions**):

  | Secret | Required | What it is |
  |--------|----------|------------|
  | `ANTHROPIC_API_KEY` | yes | For LLM rubric scoring. |
  | `GOOGLE_SERVICE_ACCOUNT_JSON` | yes | Full JSON key blob (path is allowed in local dev only). |
  | `SHEET_ID` | yes | The tracker Sheet's ID. |
  | `PROXY_URLS` | optional | Comma-separated `user:pass@host:port` proxies for board scraping. |

- [ ] **Initialize the sheet** (one time):

  ```bash
  python scripts/setup_sheet.py
  ```

- [ ] **Run the pipeline** to verify:

  ```bash
  python scripts/run.py --config config/search.yaml
  ```

  Open your tracker Sheet — you should see a deduped, ranked list of roles.

- [ ] **Enable the `daily-job-scout` workflow.** Go to the **Actions** tab and
      enable workflows. From then on it runs automatically once a day (and you
      can trigger it any time — see below).

---

## The daily cron

`.github/workflows/daily.yml` runs the pipeline on a schedule:

```yaml
on:
  schedule:
    - cron: "0 11 * * *"   # 11:00 UTC daily (~6am US Central). Adjust to taste.
  workflow_dispatch: {}    # allows manual runs too
```

It checks out the repo, installs deps, runs
`python scripts/run.py --config config/search.yaml` with the four secrets in the
environment, then commits the updated `state/` back to the repo so the next run
only surfaces genuinely new roles.

### On-demand runs

You can fire a fresh run at any time, three ways:

1. **GitHub UI** — Actions tab → pick a workflow → **Run workflow**.
2. **gh CLI** — `gh workflow run daily.yml` (or `on_demand.yml`).
3. **REST API** — `POST /repos/{owner}/{repo}/actions/workflows/daily.yml/dispatches`
   (or `.../on_demand.yml/dispatches`). This lets an agent or a Google Sheets
   Apps Script button kick off a scan.

`.github/workflows/on_demand.yml` is a `workflow_dispatch`-only twin of the
daily job, giving manual/API runs their own name in the Actions history. You
don't strictly need it — `daily.yml` already declares `workflow_dispatch` — but
it keeps the two run histories separate.

### Cron caveats

- **Scheduled runs can be delayed** when GitHub Actions is under load. Don't
  treat the time as exact.
- **GitHub disables scheduled workflows after ~60 days of repo inactivity.** The
  nightly `state/` commit counts as activity and keeps the schedule alive — so
  as long as the cron is running, it self-sustains.
- **Action-minute limits.** Private repos get ~2,000 free Action-minutes/month;
  public repos are unlimited. Keep `results_per_board` low and the cadence daily
  (not hourly) to stay well within budget — and to scrape respectfully.

---

## Ethics & ToS

job-scout is built to be ToS-defensible, ban-resistant, and respectful. Please
keep it that way.

- **Public-data only.** It reads publicly visible listings and public ATS JSON
  endpoints. It does **not** log in, solve CAPTCHAs, or bypass authentication.
- **No auto-apply.** The tool stops at discovery + scoring. It never submits an
  application. A human makes every apply decision.
- **Respect ToS and robots.** Some job boards prohibit scraping in their terms;
  using the board scrapers is **at your own risk**. Direct ATS JSON endpoints
  are the recommended, lowest-risk path — prefer them.
- **Keep volume low, cadence daily.** Small result counts, fresh-only windows,
  exponential backoff. Never automate a logged-in session.
- **Protect your accounts.** Never point this at a logged-in LinkedIn session;
  account bans are real and escalating. LinkedIn is off by default in
  `config/sources.yaml` for this reason.
- **No warranty.** Scrapers break, endpoints change, and LLM scores are
  imperfect. Treat the tracker as a ranked shortlist, not a verdict — verify
  before you act.

---

## License

MIT — see [LICENSE](LICENSE).

Dependencies carry their own licenses, which you must comply with. Notably:

- [JobSpy](https://github.com/cullenwatson/JobSpy) — MIT
- [sentence-transformers](https://github.com/UKPLab/sentence-transformers) — Apache-2.0

---

See [PROJECT.md](PROJECT.md) for the full architecture spec, data schema,
dedupe/scoring details, and the phased build plan.
