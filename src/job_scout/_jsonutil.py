"""Shared helper: robustly pull the first JSON object out of an LLM response.

Models occasionally wrap their JSON in ```fences``` or surrounding prose despite
instructions. This scans for the first *balanced* ``{...}`` span and parses it —
unlike a greedy ``{.*}`` regex, which spans from the first ``{`` to the LAST
``}`` and so breaks on any trailing prose that contains a brace.
"""

from __future__ import annotations

import json
import re


def extract_json(raw: str) -> dict | None:
    """Return the first balanced JSON object in ``raw`` as a dict, else None."""
    if not raw:
        return None
    raw = raw.strip()

    # Strip ```json ... ``` fences if the model added them despite instructions.
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw).strip()

    # Fast path: the whole thing is JSON.
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        pass

    # Fallback: scan for a balanced {...} span and parse just that.
    start = raw.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(raw[start : i + 1])
                    return obj if isinstance(obj, dict) else None
                except (ValueError, TypeError):
                    return None
    return None
