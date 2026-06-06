"""Tests for the LLM provider layer (anthropic SDK vs Claude Code CLI)."""

from __future__ import annotations

from types import SimpleNamespace

from job_scout import llm


def test_resolve_provider(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("JOB_SCOUT_LLM_PROVIDER", raising=False)
    cfg_key = SimpleNamespace(env=SimpleNamespace(anthropic_api_key="sk-x"))
    cfg_none = SimpleNamespace(env=SimpleNamespace(anthropic_api_key=None))

    assert llm.resolve_provider(cfg_key) == "anthropic"           # key -> SDK
    monkeypatch.setattr(llm.shutil, "which", lambda n: "/usr/bin/claude" if n == "claude" else None)
    assert llm.resolve_provider(cfg_none) == "claude_cli"          # no key + CLI present
    monkeypatch.setattr(llm.shutil, "which", lambda n: None)
    assert llm.resolve_provider(cfg_none) == "anthropic"           # nothing -> anthropic (will no-op)
    monkeypatch.setenv("JOB_SCOUT_LLM_PROVIDER", "claude_cli")     # explicit env wins
    assert llm.resolve_provider(cfg_key) == "claude_cli"


def test_claude_cli_parses_json_envelope(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda n: "/usr/bin/claude")

    def fake_run(cmd, **kw):
        assert "-p" in cmd and "--system-prompt" in cmd
        assert cmd[cmd.index("--output-format") + 1] == "json"
        return SimpleNamespace(returncode=0, stderr="",
                               stdout='{"type":"result","is_error":false,"result":"HELLO"}')

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    assert llm.complete("sys", "user", model="claude-sonnet-4-5", provider="claude_cli") == "HELLO"


def test_claude_cli_error_and_is_error(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda n: "/usr/bin/claude")
    # non-zero exit
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    assert llm.complete("s", "u", model="x", provider="claude_cli") is None
    # is_error envelope
    monkeypatch.setattr(llm.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=0, stderr="",
                                                          stdout='{"is_error":true,"result":"x"}'))
    assert llm.complete("s", "u", model="x", provider="claude_cli") is None


def test_claude_cli_model_only_forwarded_for_claude(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm.shutil, "which", lambda n: "/usr/bin/claude")

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return SimpleNamespace(returncode=0, stderr="", stdout='{"is_error":false,"result":"ok"}')

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    llm.complete("s", "u", model="MiniMax-M2.5-highspeed", provider="claude_cli")
    assert "--model" not in seen["cmd"]   # non-Claude model: use the CLI default
    llm.complete("s", "u", model="claude-sonnet-4-5", provider="claude_cli")
    assert "--model" in seen["cmd"]


def test_concurrency_is_gentle_for_cli():
    assert llm.concurrency("claude_cli", 6) == 2
    assert llm.concurrency("anthropic", 6) == 6
