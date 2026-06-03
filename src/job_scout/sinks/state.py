"""Cross-run state sink — the memory between daily runs.

`load_state` reads the previously-seen job map (consumed by `dedupe.py` so the
daily run only surfaces genuinely new roles). `save_state` merges this run's jobs
back in, refreshing `last_seen` while preserving the original `first_seen`.

The required, authoritative artifact is **`state/seen_hashes.json`** (committed
back by the cron). A `state/snapshot.sqlite` mirror is a best-effort nice-to-have
for ad-hoc querying — any failure there is swallowed and never fails the run.

Stdlib only (json, os, pathlib, sqlite3) — this module must work in a bare CI
checkout with no third-party deps.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from ..models import Job


# state/ lives at the repo root. This file is at
# <repo>/src/job_scout/sinks/state.py, so parents[3] is <repo>.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_STATE_DIR = _REPO_ROOT / "state"
_SEEN_PATH = _STATE_DIR / "seen_hashes.json"
_SQLITE_PATH = _STATE_DIR / "snapshot.sqlite"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_iso(value) -> str | None:
    """Best-effort ISO string for a date/datetime/str (or None)."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def load_state(config) -> dict:
    """Return the seen-job map from `state/seen_hashes.json`, or {} if absent.

    Shape: ``{ job_id: {"first_seen", "last_seen", "title", "company", "status"} }``.
    A missing or malformed file is treated as an empty map (the run still proceeds).
    """
    if not _SEEN_PATH.exists():
        return {}
    try:
        data = json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_state(jobs: list[Job], config) -> None:
    """Merge this run's jobs into the seen map and write it back atomically.

    For each job: preserve the original ``first_seen`` if we've seen the id before,
    refresh ``last_seen`` to now, and store a few readable fields. The JSON write is
    atomic (tmp file + ``os.replace``). The sqlite mirror is attempted afterwards and
    its failures are non-fatal.
    """
    seen = load_state(config)
    now = _now_iso()

    for job in jobs:
        prior = seen.get(job.id) or {}
        first_seen = _as_iso(job.first_seen) or prior.get("first_seen") or now
        seen[job.id] = {
            "first_seen": first_seen,
            "last_seen": now,
            "title": job.title,
            "company": job.company,
            "status": job.status,
        }

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(_SEEN_PATH, seen)

    # Best-effort sqlite mirror — never let it break the run.
    try:
        _write_sqlite(jobs, now)
    except Exception:  # noqa: BLE001 — sqlite is optional / nice-to-have
        pass


def _atomic_write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _write_sqlite(jobs: list[Job], now: str) -> None:
    """Upsert the run's jobs into `state/snapshot.sqlite` (id PK + key fields)."""
    conn = sqlite3.connect(_SQLITE_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                id          TEXT PRIMARY KEY,
                title       TEXT,
                company     TEXT,
                location    TEXT,
                source      TEXT,
                url         TEXT,
                score       REAL,
                status      TEXT,
                first_seen  TEXT,
                last_seen   TEXT
            )
            """
        )
        for job in jobs:
            conn.execute(
                """
                INSERT INTO jobs
                    (id, title, company, location, source, url, score, status,
                     first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT first_seen FROM jobs WHERE id = ?), ?), ?)
                ON CONFLICT(id) DO UPDATE SET
                    title    = excluded.title,
                    company  = excluded.company,
                    location = excluded.location,
                    source   = excluded.source,
                    url      = excluded.url,
                    score    = excluded.score,
                    status   = excluded.status,
                    last_seen = excluded.last_seen
                """,
                (
                    job.id,
                    job.title,
                    job.company,
                    job.location,
                    job.source,
                    job.url,
                    job.score,
                    job.status,
                    job.id,
                    _as_iso(job.first_seen) or now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()
