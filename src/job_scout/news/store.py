"""News cache (state/news.json) — URL-keyed upsert that preserves user feedback.

Same contract as the job CSV sink: re-running the pull refreshes article metadata
but NEVER clobbers the user's feedback (useful / valuable / status / notes /
first_seen). Atomic writes (tempfile + replace).
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

log = logging.getLogger("job_scout.news.store")

_STORE_PATH = Path("state/news.json")
_PRESERVE = ("useful", "valuable", "status", "notes", "first_seen")  # user-owned
_FEEDBACK_FIELDS = {"useful", "valuable", "status", "notes"}
_VALID_STATUS = {"new", "saved", "dismissed"}


def load(path=None) -> dict:
    p = Path(path) if path else _STORE_PATH
    if not p.exists():
        return {"items": {}, "runs": 0}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"items": {}, "runs": 0}
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        return {"items": {}, "runs": 0}
    return data


def seen_urls(store: dict) -> set:
    return set(store.get("items", {}).keys())


_SECTOR_WORDS = ("sector", "bank", "insurance", "manufactur", "industrial",
                 "cpg", "retail", "food", "sport", "entertainment", "consumer goods")
_ROLE_WORDS = ("role", "trend", "leadership", "governance", "transformation",
               "adoption", "strategy", "innovation")


def normalize_topic(raw) -> str:
    """Collapse the model's (sometimes compound/freeform) topic into one of the
    three buckets the UI filters on: role-trend | sector | other."""
    t = (raw or "").lower()
    if any(w in t for w in _ROLE_WORDS):
        return "role-trend"
    if any(w in t for w in _SECTOR_WORDS):
        return "sector"
    return "other"


def upsert(store: dict, items: list[dict], path=None) -> dict:
    """Merge scored items into the store, preserving user feedback. Saves + returns."""
    today = date.today().isoformat()
    bucket = store.setdefault("items", {})
    for it in items:
        url = it.get("url")
        if not url:
            continue
        existing = bucket.get(url, {})
        merged = {**it}
        for k in _PRESERVE:
            if existing.get(k) not in (None, ""):
                merged[k] = existing[k]
        merged.setdefault("first_seen", today)
        merged.setdefault("status", "new")
        bucket[url] = merged
    store["runs"] = store.get("runs", 0) + 1
    save(store, path)
    return store


def update_feedback(url: str, fields: dict, path=None) -> bool:
    """Set user feedback on one item (used by serve.py). Returns whether it existed."""
    store = load(path)
    item = store.get("items", {}).get(url)
    if not item:
        return False
    for k, v in fields.items():
        if k in _FEEDBACK_FIELDS and (k != "status" or v in _VALID_STATUS):
            item[k] = v
    save(store, path)
    return True


def items_sorted(store: dict) -> list[dict]:
    """Newest first; dismissed pushed to the bottom."""
    items = list(store.get("items", {}).values())
    items.sort(key=lambda x: (x.get("published") or x.get("first_seen") or ""), reverse=True)
    active = [x for x in items if x.get("status") != "dismissed"]
    dismissed = [x for x in items if x.get("status") == "dismissed"]
    return active + dismissed


def save(store: dict, path=None) -> None:
    p = Path(path) if path else _STORE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
