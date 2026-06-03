"""JobSpy board source — Indeed / Google / LinkedIn / etc. via `python-jobspy`.

This wraps `jobspy.scrape_jobs` (which returns a pandas DataFrame) and emits raw
dicts per the RAW DICT CONTRACT in `base.py`. It is driven entirely by config
(design principle #1): no hardcoded locations, keywords, or sites.

Behavior:
- Loops each keyword in `config.search.keywords` (search_term) and concats results.
- Calls `scrape_jobs` once per keyword across all configured `sites` (site_name).
- Maps freshness_hours -> hours_old, distance_miles -> distance,
  results_per_board -> results_wanted, location.query -> location.
- Honors remote_policy ("only" -> is_remote=True request + filter; "exclude" ->
  drop remote rows; "include" -> no filter).
- Emits one raw dict per row, with `source` set to the per-row board name
  (indeed/google/linkedin/...), NOT "jobspy".

Defensive: jobspy is imported lazily; an import failure or a per-keyword scrape
error is logged and skipped so one bad call never kills the run.
"""

from __future__ import annotations

import logging
import math
from typing import Any

log = logging.getLogger("job_scout.sources.jobspy")


def _clean(value: Any) -> Any:
    """Return None for pandas/NumPy NaN / empty strings, else the value as-is."""
    if value is None:
        return None
    try:
        # pandas NaN is a float that != itself; also catches numpy nan.
        if isinstance(value, float) and math.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _str(value: Any) -> str | None:
    """Coerce a cleaned value to a stripped string, or None."""
    value = _clean(value)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any) -> bool | None:
    value = _clean(value)
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y", "remote"}
    return None


def _date_iso(value: Any) -> str | None:
    """Normalize jobspy's `date_posted` (date / Timestamp / str) to an ISO string."""
    value = _clean(value)
    if value is None:
        return None
    # pandas Timestamp / datetime / date all expose isoformat(). pandas NaT also
    # has isoformat() but returns "NaT" — guard against that and similar sentinels.
    iso = getattr(value, "isoformat", None)
    if callable(iso):
        try:
            text = str(iso()).strip()
        except Exception:  # noqa: BLE001
            return None
        return None if (not text or text in {"NaT", "NaN", "None"}) else text
    text = _str(value)
    return None if text in {"NaT", "NaN", "None"} else text


def _comp_text(row: Any) -> str | None:
    """Build a human comp string from min/max/interval/currency columns if present."""
    def _get(key: str) -> Any:
        try:
            return _clean(row[key]) if key in row else None
        except Exception:  # noqa: BLE001
            return None

    lo = _get("min_amount")
    hi = _get("max_amount")
    interval = _str(_get("interval"))
    currency = _str(_get("currency")) or "USD"

    if lo is None and hi is None:
        # Some jobspy versions expose a prebuilt salary/compensation string.
        for key in ("salary_source", "compensation", "salary"):
            text = _str(_get(key))
            if text:
                return text
        return None

    def _fmt(amount: Any) -> str | None:
        try:
            return f"{float(amount):,.0f}"
        except (TypeError, ValueError):
            return None

    lo_s, hi_s = _fmt(lo), _fmt(hi)
    if lo_s and hi_s:
        amount = f"{lo_s} - {hi_s}"
    elif lo_s:
        amount = f"from {lo_s}"
    elif hi_s:
        amount = f"up to {hi_s}"
    else:
        return None

    parts = [currency, amount]
    if interval:
        parts.append(f"/ {interval}")
    return " ".join(parts)


def _hours_old(freshness_hours: int | None) -> int | None:
    if freshness_hours is None:
        return None
    try:
        hours = int(freshness_hours)
    except (TypeError, ValueError):
        return None
    return hours if hours > 0 else None


class JobSpySource:
    """Board aggregator source backed by `python-jobspy`."""

    name = "jobspy"

    def fetch(self, config: Any) -> list[dict]:
        boards = config.sources.boards
        if not getattr(boards, "enabled", True):
            log.info("jobspy: boards disabled in config; skipping")
            return []

        sites = list(boards.sites or [])
        if not sites:
            log.info("jobspy: no sites configured; skipping")
            return []

        search = config.search
        keywords = [k for k in (search.keywords or []) if k and str(k).strip()]
        if not keywords:
            log.info("jobspy: no keywords configured; skipping")
            return []

        # Lazy import so a missing package never breaks module import / other sources.
        try:
            from jobspy import scrape_jobs
        except Exception as e:  # noqa: BLE001
            log.warning("jobspy: package unavailable, skipping board scrape: %s", e)
            return []

        location = search.location
        loc_query = _str(getattr(location, "query", None))
        distance = getattr(location, "distance_miles", None)
        remote_policy = getattr(location, "remote_policy", "include")
        results_wanted = getattr(search, "results_per_board", None)
        hours_old = _hours_old(getattr(search, "freshness_hours", None))

        proxies = list(boards.proxies or []) or list(getattr(config.env, "proxy_urls", []) or [])
        proxies = proxies or None

        # remote_policy -> request hint + post-filter.
        is_remote_request = True if remote_policy == "only" else None

        rows: list[dict] = []
        for keyword in keywords:
            term = str(keyword).strip()
            kwargs: dict[str, Any] = {
                "site_name": sites,
                "search_term": term,
                "results_wanted": results_wanted,
            }
            if loc_query:
                kwargs["location"] = loc_query
            if distance is not None:
                kwargs["distance"] = distance
            if hours_old is not None:
                kwargs["hours_old"] = hours_old
            if is_remote_request is not None:
                kwargs["is_remote"] = is_remote_request
            if proxies is not None:
                kwargs["proxies"] = proxies

            try:
                df = scrape_jobs(**kwargs)
            except Exception as e:  # noqa: BLE001 — one bad keyword must not kill the loop
                log.warning("jobspy: scrape failed for keyword %r: %s", term, e)
                continue

            if df is None or getattr(df, "empty", True):
                continue

            for raw in self._rows_from_df(df, remote_policy, term):
                rows.append(raw)

        log.info("jobspy: %d raw listings across %d keyword(s)", len(rows), len(keywords))
        return rows

    def _rows_from_df(self, df: Any, remote_policy: str, term: str) -> list[dict]:
        out: list[dict] = []
        try:
            records = df.to_dict("records")
        except Exception as e:  # noqa: BLE001
            log.warning("jobspy: could not read DataFrame rows: %s", e)
            return out

        for row in records:
            title = _str(row.get("title"))
            company = _str(row.get("company"))
            # Prefer the direct apply URL over the aggregator URL.
            url = _str(row.get("job_url_direct")) or _str(row.get("job_url")) or _str(row.get("url"))

            # Required keys: skip half-records rather than emit them (contract).
            if not (title and company and url):
                continue

            is_remote = _bool(row.get("is_remote"))

            # remote_policy post-filter (jobspy's own flag isn't always reliable).
            if remote_policy == "exclude" and is_remote:
                continue
            if remote_policy == "only" and is_remote is False:
                continue

            board = _str(row.get("site")) or _str(row.get("source")) or self.name

            out.append(
                {
                    "title": title,
                    "company": company,
                    "url": url,
                    "source": board,  # per-row board, NOT "jobspy"
                    "location": _str(row.get("location")),
                    "is_remote": is_remote,
                    "date_posted": _date_iso(row.get("date_posted")),
                    "description": _str(row.get("description")),
                    "comp_text": _comp_text(row),
                    "_raw": {"search_term": term},
                }
            )
        return out
