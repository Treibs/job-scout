# Adaptive Discovery — plan

Turns job-scout from a fixed pull into a self-tuning loop: informed variability +
a feedback loop grounded in Robert's resume, what's scoring, and what he engages
with. Agreed with Robert 2026-06-03.

## Decisions (locked)
- **Autonomy:** auto-add companies AND keywords — but NOT willy-nilly. Every
  addition must be justified against resume goals and similarity to current
  targets/roles. May bend slightly outside the established filter when a role /
  company / opportunity clearly fits. Kitsune leaves a dated report of changes.
- **Interest signal:** clickable in the dashboard (Interested / Applied / Pass),
  written back to the tracker. This is the strongest feedback the loop learns from.

## Relevance guardrail (the core rule)
No keyword/company is added unless the strategist produces, per candidate:
`fit_reason` (1-2 sentences tying it to resume + existing targets) and a
`relevance` score ≥ threshold. Sectors of record: banking/insurance, industrials/
manufacturing, CPG/food/retail, sports/entertainment — Chicago, AI/innovation
leadership. "Bend" = adjacent sector/title allowed only with a strong written
justification. Everything logged; nothing silent.

## Phase 1 — LinkedIn JD enrichment (scrappy, no proxies)
Two-pass so we fetch ~30 descriptions/day, not 190+.
- `sources/linkedin_jd.py` — fetch ONE job's description via LinkedIn's public
  guest endpoint `…/jobs-guest/jobs/api/jobPosting/{id}` (what the search already
  hits — lighter than the full page). Polite: browser headers, timeout, one try,
  randomized delay, returns None on any failure.
- `state/linkedin_jd_cache.json` — id → description. Fetch once, reuse forever
  (dedup already tracks ids), so steady-state is a handful of NEW roles/day.
- `enrich.py` stage (between dedupe and score): for LinkedIn jobs lacking a
  description, rank by **local embedding** similarity to resume (cheap, no API,
  reuses the sentence-transformer), take top `LINKEDIN_ENRICH_MAX` (default 30),
  fetch (cache-first) with 2-5s jitter, attach. Then normal LLM scoring runs once.
- Gated by `boards.linkedin_fetch_description` (now meaning "enrich top-N", safe).

## Phase 2 — signal capture + performance ledger
- Dashboard write-back: Interested / Applied / Pass buttons. Static HTML can't
  save itself → tiny local helper (`scripts/serve.py`, localhost-only Flask/stdlib
  http.server) that PATCHes status in the CSV. Dashboard posts to it; falls back to
  localStorage + an export if the helper isn't running.
- `state/discovery.json` ledger: per keyword & per company — runs, found,
  passed_filters, avg_score, max_score, n_high(≥60), last_productive, interest_hits.
  Written every run.

## Phase 3 — the strategist (the "thought")
- Daily run = exploit + explore (epsilon-greedy): ~80% proven keywords/companies,
  ~20% rotating trials whose yield is tracked in the ledger.
- Kitsune cron `job-scout-strategist`, **every 3 days**: LLM reasons over ledger
  + recent high-scorers + interest hits + resume, and (a) proposes new keywords from
  winning title-patterns, (b) discovers companies from high-scoring off-watchlist
  board roles → resolves their ATS → adds them, (c) prunes dead arms. Each change
  passes the relevance guardrail; auto-applied; dated report committed.

## Build order
P1 (LinkedIn enrich) → P2 (interest buttons + ledger) → P3 (strategist). P2 must
land before P3 can be smart (it needs the data).
