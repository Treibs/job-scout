# News page — plan

A second surface alongside the job tracker: a **News** feed that surfaces articles
relevant to the user's professional focus, each with an LLM summary, a relevance
score, and a link out. It reuses the job pipeline's shape end-to-end so there's
little new machinery.

## Decisions (locked with the user)
- **Free-first sourcing.** Google News RSS + GDELT + (optional) the user's local
  SearxNG. No paid API, no scraping that needs auth — same ToS-defensible posture
  as the job sources. A cheap paid fallback (Serper.dev ~$1/1k, or Tavily free tier)
  can be added later only if coverage gaps appear.
- **Relevance focus = domain/role trends + target sectors.** NOT company-specific
  and NOT job-market/hiring signals. The scorer rewards articles about the themes
  tied to the resume (e.g. AI leadership / transformation / enterprise adoption) and
  the user's target sectors; it down-weights everything else.
- **Daily digest cadence.** One scheduled pull/day, like the job scan; plus on-demand.

## Architecture (mirrors the job pipeline)
```
queries  ->  sources  ->  dedupe  ->  relevance-score (LLM)  ->  store  ->  News page  ->  feedback
(topics +    (RSS,        (url +       (relevance 0-1 +          (news.json   (summaries,    (👍/👎 relevance
 sectors)     GDELT,        title       summary + why +           upsert,      links, score,   + value -> tunes
              SearxNG)      fuzzy)      topic tag)                dedup)       filter)         future queries)
```

Reused as-is: MiniMax scoring via the Anthropic-compatible endpoint, ThreadPool
concurrency, URL-keyed upsert + cross-run dedup, the localhost `serve.py` write-back,
and the strategist+guardrail feedback pattern.

## Sources (free)
- **Google News RSS** — `news.google.com/rss/search?q=<query>` per topic/sector query.
  Stdlib XML parse; returns title, publisher, date, link, snippet. Primary.
- **GDELT 2.1 Doc API** — `api.gdeltproject.org/.../doc?query=...&mode=artlist&format=json`.
  Free, no key; paced (it rate-limits). Broad coverage for sector/trend signals.
- **SearxNG (optional)** — the user's local instance, `category=news&format=json`.
  Off by default; flip on in config to add aggregated coverage at $0.

## Relevance + summary (one LLM call per new article)
Input: title + snippet + source + resume excerpt + `news.topics` + `target_sectors`.
Output (strict JSON): `relevance` 0-1, `summary` (1-2 sentences), `why_relevant`
(one line tying it to the user's focus), `topic` (role-trend | sector | other).
Only articles `>= relevance_threshold` (default 0.6) are kept. Already-seen URLs are
skipped (no re-scoring) to save tokens — same as the job `seen_hashes` trick.

## Store / cache  (`state/news.json`, git-ignored)
URL-keyed items: title, source, url, published, first_seen, relevance, summary,
why_relevant, topic, plus user feedback (`useful` 👍/👎, `valuable` 👍/👎, `status`
new|saved|dismissed, notes). Upsert preserves all feedback across runs.

## Feedback loop
The News page writes 👍/👎 (relevance + value) and save/dismiss back via `serve.py`.
A later **news-strategist** (phase 2) reads that feedback and tunes the query terms,
source weights, and threshold under the same resume-fit guardrail as the job strategist.

## Build order
1. **Backend** — config (`news.yaml`), `news/` package (sources, store, score, pipeline),
   `scripts/news.py` runner, tests. *(this MVP)*
2. **Page** — `news_report.py` renders `output/news.html` in the dashboard's visual
   language; top nav links Jobs ⇄ News; `serve.py` serves `/news` + `/news-feedback`. *(this MVP)*
3. **Tuning** — the news-strategist auto-refines queries from feedback. *(follow-up)*
