"""ATS fetcher registry.

Each ATS module exposes `fetch(company: CompanyTarget, config: Config) -> list[dict]`
returning raw dicts per the RAW DICT CONTRACT (see `sources/base.py`). The registry
is built defensively so a missing/broken module doesn't break the others.
"""

from __future__ import annotations

import logging

log = logging.getLogger("job_scout.sources.ats")

ATS_FETCHERS: dict = {}

for _name in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday"):
    try:
        _mod = __import__(f"job_scout.sources.ats.{_name}", fromlist=["fetch"])
        ATS_FETCHERS[_name] = _mod.fetch
    except Exception as e:  # noqa: BLE001
        log.debug("ATS module %s not available yet: %s", _name, e)

__all__ = ["ATS_FETCHERS"]
