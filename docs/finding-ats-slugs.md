# Finding a company's ATS and slug

`config/companies.yaml` drives the direct-ATS pulls — the cleanest, lowest-risk
source job-scout has (see PROJECT.md §2.4 and §14). Each entry tells the pipeline
*which* Applicant Tracking System a company posts on and *which tenant/slug* is
theirs, so it can hit the official public JSON endpoint instead of scraping a
board.

This guide shows how to go from a careers-page URL to a correct `companies.yaml`
entry for each supported ATS:

- [Greenhouse](#greenhouse)
- [Lever](#lever)
- [Ashby](#ashby)
- [SmartRecruiters](#smartrecruiters)
- [Workday](#workday)

> All examples below use **fake companies**. Swap in your own targets.

---

## The general method

1. Find the company's careers / "open roles" page (Google `<company> careers`).
2. Click into the actual job listings. The job board almost always lives on a
   **third-party ATS domain**, not the company's own website — that domain tells
   you the ATS.
3. Read the **slug** (a.k.a. board token / company token / tenant) out of the URL.
4. Add an entry to `config/companies.yaml` using the per-ATS shape below.

If clicking "View jobs" keeps you on the company's own domain, open your
browser's dev tools → Network tab and watch where the listings are fetched from;
the XHR/fetch call usually goes to one of the ATS domains shown below.

---

## Greenhouse

**URL patterns**

```
https://boards.greenhouse.io/{slug}
https://boards.greenhouse.io/{slug}/jobs/1234567
https://job-boards.greenhouse.io/{slug}
# Embedded on the company site, but the API call goes to:
https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
```

The `{slug}` is the **board token** — the path segment right after the domain.

**`companies.yaml` entry**

```yaml
companies:
  - name: "Example Industrial Co"
    ats: "greenhouse"
    slug: "exampleindustrial"
```

So for `https://boards.greenhouse.io/exampleindustrial`, the slug is
`exampleindustrial`.

---

## Lever

**URL patterns**

```
https://jobs.lever.co/{slug}
https://jobs.lever.co/{slug}/00000000-1111-2222-3333-444444444444
# API:
https://api.lever.co/v0/postings/{slug}?mode=json
```

The `{slug}` is the path segment after `jobs.lever.co/`.

**`companies.yaml` entry**

```yaml
companies:
  - name: "Example Startup Inc"
    ats: "lever"
    slug: "examplestartup"
```

So for `https://jobs.lever.co/examplestartup`, the slug is `examplestartup`.

---

## Ashby

**URL patterns**

```
https://jobs.ashbyhq.com/{slug}
https://jobs.ashbyhq.com/{slug}/00000000-1111-2222-3333-444444444444
```

The `{slug}` is the path segment after `jobs.ashbyhq.com/`. It is usually the
company's display name (sometimes with capitalization, e.g.
`jobs.ashbyhq.com/ExampleAI`); copy it exactly as it appears.

**`companies.yaml` entry**

```yaml
companies:
  - name: "Example AI Labs"
    ats: "ashby"
    slug: "ExampleAI"
```

---

## SmartRecruiters

**URL patterns**

```
https://jobs.smartrecruiters.com/{slug}
https://careers.smartrecruiters.com/{slug}
https://jobs.smartrecruiters.com/{slug}/12345678-some-role-title
# API:
https://api.smartrecruiters.com/v1/companies/{slug}/postings
```

The `{slug}` is the company identifier after the domain — typically the
company name with no spaces (e.g. `ExampleRetailGroup`). Copy it exactly,
including capitalization.

**`companies.yaml` entry**

```yaml
companies:
  - name: "Example Retail Group"
    ats: "smartrecruiters"
    slug: "ExampleRetailGroup"
```

---

## Workday

Workday is the most involved one. Its URLs are not a single slug — they encode a
**tenant**, a **datacenter**, and a **site** name, all three of which you need.

**URL pattern**

```
https://{tenant}.{dc}.myworkdayjobs.com/{site}
https://{tenant}.{dc}.myworkdayjobs.com/en-US/{site}
https://{tenant}.{dc}.myworkdayjobs.com/{site}/job/Location/Some-Role_R-12345
```

Map each part of the host and path:

| Part       | Where it is in the URL                              | Example     |
|------------|-----------------------------------------------------|-------------|
| `tenant`   | first label of the host (before the first `.`)      | `examplebank` |
| `datacenter` | second label of the host (`wd1`, `wd3`, `wd5`, …) | `wd1`       |
| `site`     | the careers-site path segment                       | `External`  |

The `site` is the named career site (commonly `External`, `careers`,
`Example_Careers`, etc.). If the URL contains a locale like `/en-US/`, the
**site** is the segment *after* the locale, not the locale itself.

**`companies.yaml` entry**

```yaml
companies:
  - name: "Example Bank"
    ats: "workday"
    tenant: "examplebank"
    site: "External"
    datacenter: "wd1"
```

So for `https://examplebank.wd1.myworkdayjobs.com/en-US/External`:

- `tenant` = `examplebank`
- `datacenter` = `wd1`
- `site` = `External`

> Workday tenants vary in how openly they expose their JSON search endpoint.
> Per PROJECT.md §12 (Stage 3), Workday is added "where tenants permit" — if a
> given tenant blocks the public search call, that company will simply be
> skipped (graceful per-source failure, §2.3), and the rest of the run is
> unaffected.

---

## Worked examples (fake companies)

### Example 1 — a Greenhouse startup

You find **Example Startup Inc** careers and clicking "Apply" lands you on:

```
https://boards.greenhouse.io/examplestartup/jobs/7654321
```

Domain is `boards.greenhouse.io` → ATS is Greenhouse. The slug is the path
segment after the domain, `examplestartup`:

```yaml
companies:
  - name: "Example Startup Inc"
    ats: "greenhouse"
    slug: "examplestartup"
```

### Example 2 — a Workday enterprise

You find **Example Bank** careers, and the listings load from:

```
https://examplebank.wd1.myworkdayjobs.com/en-US/External/job/Chicago/AI-Director_R-0420
```

Break down the host `examplebank.wd1.myworkdayjobs.com` and the path:

- host first label → `tenant: examplebank`
- host second label → `datacenter: wd1`
- path after the `/en-US/` locale → `site: External`

```yaml
companies:
  - name: "Example Bank"
    ats: "workday"
    tenant: "examplebank"
    site: "External"
    datacenter: "wd1"
```

---

## Tips

- **One company can use more than one ATS** (e.g. corporate roles on Workday,
  early-talent on Greenhouse). Add a separate entry per board you care about.
- **Capitalization matters** for Ashby and SmartRecruiters slugs — copy them
  verbatim from the URL.
- **Don't guess slugs.** If you're unsure, open the careers page and read the
  real URL. A wrong slug just yields zero results for that company; the run
  continues regardless.
- Prefer companies that expose a clean ATS board over ones that only post to
  aggregators — the direct JSON path is the recommended, lowest-risk source
  (PROJECT.md §14).
