from __future__ import annotations

import json
import subprocess

import pytest

from worker import runtime_models


def test_discovers_each_selected_runtime_and_applies_scoped_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runtime_models, "resolve_binary", lambda runtime: f"/bin/{runtime}")

    def fake_run(command, **_kwargs):
        if command[0] == "/bin/velites":
            output = json.dumps(
                [
                    {"provider": "sqai", "model": "kimi"},
                    {"provider": "sqai", "model": "deepseek"},
                ]
            )
        else:
            output = json.dumps(
                {
                    "models": [
                        {"key": "anthropic/claude", "available": True},
                        {"key": "openai/missing", "available": False},
                    ]
                }
            )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(runtime_models.subprocess, "run", fake_run)
    models, errors = runtime_models.discover_effective_models(
        {
            "runtimes": ["velites", "openclaw"],
            "models": [
                {"runtime": "velites", "provider": "sqai", "model": "kimi"},
                {"runtime": "openclaw", "provider": "anthropic", "model": "claude"},
            ],
        }
    )
    assert errors == {}
    assert models == [
        {"runtime": "openclaw", "provider": "anthropic", "model": "claude"},
        {"runtime": "velites", "provider": "sqai", "model": "kimi"},
    ]


def test_one_runtime_discovery_failure_is_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_models, "resolve_binary", lambda runtime: f"/bin/{runtime}")

    def fake_run(command, **_kwargs):
        if command[0] == "/bin/velites":
            return subprocess.CompletedProcess(command, 2, "", "bad registry")
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"models": [{"key": "anthropic/claude", "available": True}]}),
            "",
        )

    monkeypatch.setattr(runtime_models.subprocess, "run", fake_run)
    models, errors = runtime_models.discover_effective_models(
        {"runtimes": ["velites", "openclaw"], "models": []}
    )
    assert models == [{"runtime": "openclaw", "provider": "anthropic", "model": "claude"}]
    assert "velites" in errors


def test_pi_text_dialect_is_confined_to_adapter() -> None:
    assert runtime_models.parse_pi_model_list(
        "Provider Model Context\nopenai/gpt-5 400k\nanthropic claude-sonnet 200k\n"
    ) == [("anthropic", "claude-sonnet"), ("openai", "gpt-5")]
