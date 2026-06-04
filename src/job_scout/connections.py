"""LinkedIn connections overlay — "who in my network is at this company?"

Reads the user's official LinkedIn data export (``Connections.csv``), builds a
company -> people index, and matches it against the companies of the jobs we
found. This is a *local, read-only overlay*: the file is personal PII (the names
of everyone you know), so it's git-ignored and never leaves the machine — exactly
like the resume and the tracker CSV.

Get the file from LinkedIn → Settings → Data privacy → Get a copy of your data →
"Connections". Drop the unzipped ``Connections.csv`` at
``data/linkedin_connections.csv`` (the default path).

Matching is a *signal, not a claim*: company names are normalized (Inc./LLC/etc.
stripped) and then fuzzy-matched, so "Caterpillar Inc." finds "Caterpillar". A
person may have since left, so we surface their title + when you connected and let
you judge. Nothing here phones home.
"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from rapidfuzz import fuzz

log = logging.getLogger("job_scout.connections")

DEFAULT_PATH = "data/linkedin_connections.csv"
_FUZZY_THRESHOLD = 88  # token_set_ratio; below this we don't claim a match

# Company-name noise to strip before matching.
# Corporate suffixes + the leading article — noise we drop before matching, so
# "The Acme Company" and "Acme" collapse to the same key.
_SUFFIXES = {
    "the", "inc", "incorporated", "llc", "llp", "ltd", "limited", "corp", "corporation",
    "co", "company", "plc", "group", "holdings", "holding", "gmbh", "sa", "ag", "nv",
}
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")


def normalize_company(name: str | None) -> str:
    """Lowercase, drop punctuation + common corporate suffixes, collapse spaces."""
    if not name:
        return ""
    s = _PUNCT_RE.sub(" ", name.lower())
    tokens = [t for t in _WS_RE.sub(" ", s).strip().split(" ") if t and t not in _SUFFIXES]
    return " ".join(tokens)


def load_connections(path: str | Path = DEFAULT_PATH) -> list[dict]:
    """Parse a LinkedIn ``Connections.csv`` export into people dicts.

    Tolerates LinkedIn's preamble ("Notes:" lines before the real header) and a
    missing file (returns []). Each person: name, company, position, connected_on,
    url. People without a company are dropped (nothing to match on)."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:  # noqa: BLE001
        log.warning("connections: could not read %s: %s", p, e)
        return []

    lines = text.splitlines()
    # LinkedIn prepends a "Notes:" block; the real CSV starts at the header row
    # that contains "First Name".
    start = next((i for i, ln in enumerate(lines) if "First Name" in ln and "Last Name" in ln), None)
    if start is None:
        log.warning("connections: no 'First Name'/'Last Name' header found in %s", p)
        return []

    people: list[dict] = []
    for row in csv.DictReader(lines[start:]):
        company = (row.get("Company") or "").strip()
        if not company:
            continue
        name = f"{(row.get('First Name') or '').strip()} {(row.get('Last Name') or '').strip()}".strip()
        people.append({
            "name": name or "(unknown)",
            "company": company,
            "position": (row.get("Position") or "").strip(),
            "connected_on": (row.get("Connected On") or "").strip(),
            "url": (row.get("URL") or "").strip(),
        })
    log.info("connections: loaded %d people from %s", len(people), p)
    return people


def build_index(people: list[dict]) -> dict[str, list[dict]]:
    """Group people by normalized company name."""
    index: dict[str, list[dict]] = {}
    for person in people:
        key = normalize_company(person.get("company"))
        if key:
            index.setdefault(key, []).append(person)
    return index


def match_company(company: str | None, index: dict[str, list[dict]]) -> list[dict]:
    """Return the people whose (normalized) company matches ``company``.

    Exact normalized hit first; otherwise the single best fuzzy match above the
    threshold (token_set_ratio). Empty index or no match -> []."""
    key = normalize_company(company)
    if not key or not index:
        return []
    if key in index:
        return index[key]
    best_key, best_score = None, 0.0
    for cand in index:
        score = fuzz.token_set_ratio(key, cand)
        if score > best_score:
            best_key, best_score = cand, score
    return index[best_key] if best_key and best_score >= _FUZZY_THRESHOLD else []


def annotate(rows: list[dict], path: str | Path = DEFAULT_PATH) -> list[dict]:
    """Attach a ``connections`` list (matched people) to each row in place.

    No-op-safe: if the export is absent, every row just gets an empty list. Each
    person is reduced to the fields the dashboard shows."""
    index = build_index(load_connections(path))
    for r in rows:
        matches = match_company(r.get("company"), index) if index else []
        r["connections"] = [
            {"name": m["name"], "position": m["position"], "connected_on": m["connected_on"]}
            for m in matches
        ]
    return rows
