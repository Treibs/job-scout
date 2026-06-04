# job-scout

> An open-source, config-driven job **discovery and scoring** engine. It scrapes job
> listings from multiple sources, cross-references them against your resume, scores
> fit against your own weighted criteria, and writes a ranked, deduplicated tracker
> to Google Sheets — on a daily schedule and on demand.

**This is the kickoff spec.** It defines the architecture, repo layout, configuration
model, the daily-cron implementation, and a phased build plan. Read this first, then
build Stage 1.

> Working name is `job-scout` — rename freely. There must be **no personal data and no
> private references anywhere in the committed code**. Everything user-specific lives in
> git-ignored config and resume files. The repo ships only `*.example` templates.

---

## 1. What this is (and isn't)

**It is** a discovery + scoring + tracking pipeline. It finds relevant roles, ranks them
by *your* configured priorities, and gives you a clean tracker to work from.

**It is not** an auto-applier. It does not log into any site, fill forms, or submit
applications. This is a deliberate design decision:

- Auto-appliers get accounts banned (especially LinkedIn) and can't follow through to the
  external ATS where most senior roles actually live.
- Spraying applications damages the applicant's reputation and burdens employers.
- A human makes the final apply decision, every time.

Keeping the tool to **public-data discovery + local scoring** keeps it ToS-defensible,
ban-resistant, and genuinely useful.

---

## 2. Design principles

1. **Config-driven, zero personalization in code.** No hardcoded locations, industries,
   seniorities, company lists, or weights. All of it is user config.
2. **One pipeline, two triggers.** The daily cron and the on-demand run call the *same*
   `run_pipeline()`. No duplicated logic.
3. **Graceful per-source failure.** One dead source (a changed selector, a 429) must not
   kill the run. Each source is wrapped; failures are logged and skipped.
4. **Prefer official ATS JSON over board scraping.** It's cleaner, more reliable, and
   lower-risk. Board scrapers are best-effort.
5. **Low-volume, respectful scraping.** Small result counts, fresh-only windows, daily
   (not hourly), exponential backoff. Never automate a logged-in session.
6. **Deterministic gather, LLM reason.** Python scrapes and normalizes; the LLM only does
   the judgment work (fit scoring). Keep the split clean.

---

## 3. Architecture

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

**Stages:**
- **sources/** — each returns a list of raw job dicts. JobSpy for boards; one module per ATS.
- **normalize.py** — map every source into one `Job` schema.
- **dedupe.py** — composite-key exact match + fuzzy second pass across sources and runs.
- **score.py** — cheap embedding/keyword pre-filter, then LLM rubric scoring on survivors.
- **sinks/** — Google Sheets writer (upsert) + state writer (committed back to repo).

---

## 4. Repo layout

```
job-scout/
├── README.md
├── PROJECT.md                  # this file
├── LICENSE                     # MIT
├── requirements.txt
├── .gitignore                  # ignores resume + real configs + secrets
├── .env.example
├── config/
│   ├── search.example.yaml     # what/where to search, filters
│   ├── companies.example.yaml  # target employers + ATS slugs
│   ├── scoring.example.yaml    # rubric dimensions + weights
│   └── sources.example.yaml    # which sources on/off, per-source params
├── resume/
│   └── resume.example.md       # user drops their real resume.md here (git-ignored)
├── src/
│   └── job_scout/
│       ├── __init__.py
│       ├── pipeline.py         # run_pipeline(config) — the single entry point
│       ├── config.py           # load + validate config, env vars
│       ├── models.py           # Job dataclass / pydantic schema
│       ├── sources/
│       │   ├── __init__.py
│       │   ├── base.py         # Source protocol; safe_fetch() wrapper
│       │   ├── jobspy_source.py
│       │   └── ats/
│       │       ├── greenhouse.py
│       │       ├── lever.py
│       │       ├── ashby.py
│       │       ├── smartrecruiters.py
│       │       └── workday.py
│       ├── normalize.py
│       ├── dedupe.py
│       ├── score.py
│       └── sinks/
│           ├── google_sheets.py
│           └── state.py
├── scripts/
│   ├── run.py                  # CLI: python scripts/run.py --config config/search.yaml
│   └── setup_sheet.py          # one-time: create/format the tracker sheet
├── state/                      # committed back by the cron
│   ├── seen_hashes.json
│   └── snapshot.sqlite
├── tests/
└── .github/
    └── workflows/
        ├── daily.yml           # the cron
        └── on_demand.yml       # workflow_dispatch (manual / API trigger)
```

`.gitignore` must include: `resume/resume.md`, `config/*.yaml` (but NOT `*.example.yaml`),
`.env`, `service_account.json`, `__pycache__/`, `*.pyc`.

---

## 5. Configuration model

All personalization lives here. Ship only the `.example` files; users copy them to the
real names (which are git-ignored).

### `config/search.yaml`
```yaml
keywords:
  - "Director AI"
  - "Senior Manager AI"
  - "AI Adoption"
  - "AI Innovation"
location:
  query: "City, ST"           # free text; sources interpret it (e.g. "Chicago, IL" or "Remote")
  distance_miles: 50
  remote_policy: "include"    # include | exclude | only
seniority:                    # used by sources that support it + as a scoring gate
  - "director"
  - "executive"
freshness_hours: 72           # only fetch postings newer than this
results_per_board: 30         # keep low to stay under detection thresholds
hard_filters:
  exclude_industries:         # matched against company industry + JD text
    - "healthcare"
    - "biotech"
    - "pharma"
  exclude_keywords: []        # JD-level kill words
  include_keywords: []        # optional must-haves
```

### `config/companies.yaml`
Target employers for the direct-ATS pulls. The end user fills this in for their own
sectors. Format:
```yaml
companies:
  - name: "Example Industrial Co"
    ats: "greenhouse"         # greenhouse | lever | ashby | smartrecruiters | workday
    slug: "exampleindustrial" # board token / company slug
  - name: "Example Bank"
    ats: "workday"
    tenant: "examplebank"
    site: "External"
    datacenter: "wd1"
```
> Ship this with 2-3 obviously-fake placeholder entries only. A short `docs/finding-ats-slugs.md`
> should explain how anyone identifies a company's ATS and slug from a careers-page URL.

### `config/scoring.yaml`
The rubric and weights are **fully user-defined** — no opinionated defaults baked into code.
```yaml
dimensions:
  - id: "mission_impact"
    weight: 5
    prompt: "How well does this role's mission and impact align with the candidate's stated goals?"
  - id: "compensation"
    weight: 4
    prompt: "Based on any comp signals in the listing, how strong is the compensation fit?"
  - id: "learning_growth"
    weight: 3
    prompt: "How much learning and growth does this role offer relative to the candidate's trajectory?"
  - id: "work_life_balance"
    weight: 2
    prompt: "What does the listing signal about work-life balance?"
  - id: "prestige"
    weight: 1
    prompt: "How prestigious is this role/employer for the candidate's positioning?"
scale: [0, 5]                  # each dimension scored 0-5
role_fit_gate: true           # if true, hard-reject roles that fail the seniority/role match
model: "claude-sonnet-4-5"    # configurable
pre_filter:
  enabled: true
  method: "embedding"         # embedding | keyword | none
  threshold: 0.75
```

### `config/sources.yaml`
```yaml
boards:                       # via JobSpy
  enabled: true
  sites: ["indeed", "google", "linkedin"]   # linkedin optional/risky; off by default ok
  proxies: []                 # optional; "user:pass@host:port"
ats:
  enabled: true               # uses config/companies.yaml
```

### `resume/resume.md`
Plain markdown resume. Git-ignored. It's the reference document the scorer compares each
listing against. Ship `resume.example.md` with a clearly fictional sample.

---

## 6. Data schema (`models.py`)

```python
@dataclass
class Job:
    id: str                 # canonical hash (see dedupe)
    title: str
    company: str
    location: str | None
    is_remote: bool | None
    url: str                # prefer ATS/apply URL over aggregator URL
    source: str             # "indeed" | "greenhouse" | ...
    date_posted: date | None
    description: str | None
    comp_text: str | None   # raw comp string if present
    # populated by scorer:
    score: float | None = None
    dimension_scores: dict | None = None
    rationale: str | None = None
    red_flags: list[str] | None = None
    # tracker metadata:
    status: str = "new"     # new | reviewing | applied | rejected | archived
    first_seen: date | None = None
    last_seen: date | None = None
```

---

## 7. Dedupe (`dedupe.py`)

- **Composite key:** normalized `title + company + location` (lowercase, strip punctuation,
  collapse whitespace, strip seniority noise like "Sr." vs "Senior").
- **Fuzzy second pass:** `rapidfuzz` token-set ratio ≥ 90 catches "Acme Corp" vs "Acme Inc."
  and the same role cross-posted to a board *and* its ATS.
- **Merge rule:** keep one canonical record; prefer the direct ATS/apply URL over the
  aggregator link. The canonical hash becomes `Job.id`.
- **Cross-run:** `state/seen_hashes.json` holds previously seen ids so the daily run only
  surfaces genuinely new roles (and refreshes `last_seen` on still-open ones).

---

## 8. Scoring (`score.py`)

Two stages, both config-driven:

1. **Hard filters** (deterministic): location/remote policy, excluded industries/keywords,
   seniority gate. Cheap rejects happen here, before any LLM cost.
2. **Pre-filter** (optional): embedding cosine similarity between resume and JD
   (`sentence-transformers`, e.g. `all-MiniLM-L6-v2`) above `scoring.yaml:threshold`, OR
   keyword overlap, OR skipped entirely at low volume.
3. **LLM rubric scoring:** for survivors, prompt the configured model to score each
   dimension on the configured scale and return **strict JSON**:
   `{overall_score, dimension_scores, rationale, red_flags, comp_estimate}`.
   `overall_score` = weighted sum normalized to 0-100. Force the model to cite JD evidence
   in `rationale` to limit hallucinated fit. Have it score from the `resume.md` + the JD only.

Rank the tracker by `overall_score` descending.

---

## 9. Daily cron — the headline

`.github/workflows/daily.yml`:

```yaml
name: daily-job-scout
on:
  schedule:
    - cron: "0 11 * * *"      # 11:00 UTC daily (~6am US Central). Adjust to taste.
  workflow_dispatch: {}        # allows manual runs too

concurrency:
  group: job-scout
  cancel-in-progress: false

permissions:
  contents: write              # to commit state back

jobs:
  run:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -r requirements.txt
      - name: Run pipeline
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          GOOGLE_SERVICE_ACCOUNT_JSON: ${{ secrets.GOOGLE_SERVICE_ACCOUNT_JSON }}
          SHEET_ID: ${{ secrets.SHEET_ID }}
          PROXY_URLS: ${{ secrets.PROXY_URLS }}   # optional
        run: python scripts/run.py --config config/search.yaml
      - name: Commit updated state
        run: |
          git config user.name "job-scout-bot"
          git config user.email "bot@users.noreply.github.com"
          git add state/
          git diff --staged --quiet || git commit -m "chore: nightly state update"
          git push
```

`.github/workflows/on_demand.yml` is the same job body under `on: workflow_dispatch:` only
(or just rely on the `workflow_dispatch` in `daily.yml`). On-demand can also be fired via the
GitHub REST API `POST /repos/{owner}/{repo}/actions/workflows/daily.yml/dispatches`, so an
agent or a Google Sheets Apps Script button can trigger a fresh run.

**Cron caveats to document in the README:**
- Scheduled workflows can be delayed under platform load.
- GitHub disables scheduled workflows after ~60 days of repo inactivity — a periodic commit
  (the state push) keeps it alive.
- Private repos have ~2,000 free Action-minutes/month; public repos are unlimited.

---

## 10. Google Sheets tracker (`sinks/google_sheets.py`)

One-time setup (document in `docs/sheets-setup.md`):
1. Create a Google Cloud project; enable the Google Sheets API.
2. Create a **service account**; download its JSON key.
3. Create the tracker Sheet; **share it with the service-account email** (Editor).
4. Put the JSON key in the `GOOGLE_SERVICE_ACCOUNT_JSON` secret and the Sheet ID in `SHEET_ID`.

Writer behavior: **upsert by `Job.id`** — append new rows, update `status`/`last_seen`/score
on existing, mark rows stale when a listing disappears from source. Columns, dashboard-ready:

`score | mission | comp | learning | wlb | prestige | title | company | location |
comp_estimate | source | date_posted | first_seen | apply_url | status | rationale | red_flags`

`scripts/setup_sheet.py` creates the tab, writes the header row, and applies conditional
formatting on `score`.

---

## 11. Secrets / env (`.env.example`)

```
ANTHROPIC_API_KEY=
GOOGLE_SERVICE_ACCOUNT_JSON=    # full JSON, or path in local dev
SHEET_ID=
PROXY_URLS=                     # optional, comma-separated
```

Local dev reads `.env`; CI reads GitHub Secrets. Never commit real values.

---

## 12. Build plan (phased, with benchmarks)

**Stage 1 — Deterministic pipeline, manual run.**
JobSpy (Indeed + Google; LinkedIn off or low-volume) + direct Greenhouse/Lever/SmartRecruiters
pulls from `companies.yaml`. Normalize → dedupe → write to Sheets. Hard filters applied.
✅ *Advance when:* one `python scripts/run.py` run produces a deduped Sheet of relevant roles
with <10% obvious false positives.

**Stage 2 — Scoring.**
Add embedding pre-filter + LLM rubric scoring from `scoring.yaml` and `resume.md`. JSON output,
sort by score.
✅ *Advance when:* the top-10 by score are roles a human reviewer agrees are worth pursuing ≥70%
of the time.

**Stage 3 — Daily cron.**
Wire `daily.yml`, commit state back, add Ashby + Workday (where tenants permit). Per-source
failure isolation verified.
✅ *Advance when:* 7 consecutive unattended runs complete with no duplicate leakage and no manual
fixes.

**Stage 4 — On-demand + polish.**
`workflow_dispatch` + REST dispatch; `setup_sheet.py`; docs (`finding-ats-slugs.md`,
`sheets-setup.md`); README with setup walkthrough and the ethics/ToS section.
✅ *Done when:* a stranger can clone, copy the `.example` configs, add a resume + service account,
and get a working daily tracker by following the README alone.

---

## 13. End-user setup checklist (for the README)

- [ ] `git clone` and `pip install -r requirements.txt`
- [ ] Copy each `config/*.example.yaml` → `config/*.yaml` and edit
- [ ] Copy `resume/resume.example.md` → `resume/resume.md` and paste your resume
- [ ] Fill `config/companies.yaml` with your target employers + ATS slugs
- [ ] Create a Google Cloud service account, enable Sheets API, share the Sheet with it
- [ ] Add GitHub Secrets: `ANTHROPIC_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `SHEET_ID`
- [ ] Run `python scripts/setup_sheet.py` once
- [ ] Run `python scripts/run.py --config config/search.yaml` to verify
- [ ] Enable the `daily-job-scout` workflow

---

## 14. Licensing, ToS & ethics (must ship in README)

- **License:** MIT. Note that JobSpy (MIT) and `sentence-transformers` (Apache-2.0) are
  dependencies; comply with their licenses.
- **Public-data only.** The tool reads publicly visible listings and public ATS JSON
  endpoints. It does **not** log in, solve CAPTCHAs, or bypass auth.
- **No auto-apply.** State this prominently. The tool stops at discovery + scoring.
- **Respect ToS and robots.** Some job boards prohibit scraping in their terms; using board
  scrapers is at the user's own risk. Direct ATS JSON endpoints are the recommended,
  lowest-risk path. Keep volume low and cadence daily.
- **Protect your accounts.** Never point this at a logged-in LinkedIn session; account bans
  are real and escalating.
- **No warranty.** Scrapers break; endpoints change; LLM scores are imperfect. A human makes
  the final decision.

---

## 15. Dependencies (`requirements.txt` starting point)

```
python-jobspy
requests
beautifulsoup4
pydantic
pandas
rapidfuzz
sentence-transformers
anthropic
gspread
google-auth
pyyaml
python-dotenv
```

---

*Build Stage 1 first. Keep every personal detail in git-ignored config. Ship only `.example`
templates.*
