"""Unified LLM completion — one call site, two providers.

- ``anthropic``  : the Anthropic Python SDK against ``ANTHROPIC_BASE_URL`` (the
  Anthropic API by default; also any compatible endpoint, e.g. MiniMax).
- ``claude_cli`` : shells out to the local **Claude Code** CLI (``claude -p``), so
  job-scout runs on a Claude Code *subscription* with **no API key**.

``resolve_provider`` auto-picks: an API key → ``anthropic``; otherwise, if the
``claude`` CLI is on PATH → ``claude_cli``; else ``anthropic`` (which then no-ops,
and scoring degrades gracefully to unscored). Force one with
``JOB_SCOUT_LLM_PROVIDER=anthropic|claude_cli``.

``complete()`` returns the model's raw text (the caller extracts JSON), or None.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

log = logging.getLogger("job_scout.llm")

_CLAUDE_MODEL_HINTS = ("claude", "sonnet", "haiku", "opus")
_anthropic_clients: dict = {}  # cache by api_key — reuse the httpx pool across calls


def resolve_provider(config=None, explicit: str | None = None) -> str:
    """Pick the provider. Explicit arg > JOB_SCOUT_LLM_PROVIDER env > auto-detect."""
    p = (explicit or os.getenv("JOB_SCOUT_LLM_PROVIDER") or "").strip().lower()
    if p in ("anthropic", "claude_cli"):
        return p
    if _api_key(config):
        return "anthropic"
    if shutil.which("claude"):
        return "claude_cli"
    return "anthropic"


def available(provider: str, config=None) -> bool:
    """Whether the chosen provider can actually run."""
    if provider == "claude_cli":
        return shutil.which("claude") is not None
    return bool(_api_key(config))


def concurrency(provider: str, default: int = 6) -> int:
    """claude_cli spawns a process per call — keep it gentle on the subscription."""
    return 2 if provider == "claude_cli" else default


def complete(system: str, user: str, *, model: str, max_tokens: int = 4096,
             provider: str = "anthropic", api_key: str | None = None,
             timeout: int = 180) -> str | None:
    """Return the model's text for (system, user). None on failure."""
    if provider == "claude_cli":
        return _claude_cli(system, user, model=model, timeout=timeout)
    return _anthropic(system, user, model=model, max_tokens=max_tokens, api_key=api_key)


# ── providers ────────────────────────────────────────────────────────────────
def _anthropic(system, user, *, model, max_tokens, api_key) -> str | None:
    client = _anthropic_clients.get(api_key)
    if client is None:
        try:
            import anthropic  # type: ignore
        except Exception as e:  # noqa: BLE001
            log.warning("anthropic SDK unavailable: %s", e)
            return None
        client = anthropic.Anthropic(api_key=api_key)
        _anthropic_clients[api_key] = client
    resp = client.messages.create(model=model, max_tokens=max_tokens, system=system,
                                  messages=[{"role": "user", "content": user}])
    return "".join(getattr(b, "text", "") for b in getattr(resp, "content", []) or []
                   if getattr(b, "type", None) == "text" or getattr(b, "text", None))


def _claude_cli(system, user, *, model, timeout) -> str | None:
    exe = shutil.which("claude")
    if not exe:
        log.warning("claude CLI not found on PATH")
        return None
    cmd = [exe, "-p", user, "--system-prompt", system, "--output-format", "json"]
    # Only forward the model if it's a Claude model — a MiniMax/other name would be
    # invalid for the CLI, so fall back to Claude Code's configured default.
    if model and any(h in model.lower() for h in _CLAUDE_MODEL_HINTS):
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as e:
        log.warning("claude CLI call failed: %s", e)
        return None
    if proc.returncode != 0:
        log.warning("claude CLI exit %s: %s", proc.returncode, (proc.stderr or "")[:200])
        return None
    out = (proc.stdout or "").strip()
    try:
        env = json.loads(out)
    except json.JSONDecodeError:
        return out or None  # --output-format text or unexpected — return as-is
    if isinstance(env, dict):
        if env.get("is_error"):
            log.warning("claude CLI returned an error result")
            return None
        return env.get("result") or None
    return None


def _api_key(config) -> str | None:
    if config is not None:
        key = getattr(getattr(config, "env", None), "anthropic_api_key", None)
        if key:
            return key
    return os.getenv("ANTHROPIC_API_KEY")
