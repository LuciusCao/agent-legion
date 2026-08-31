"""openclaw runtime：argv golden（镜像 test_velites_command.py 结构）。

argv 草案来自方案（实测 CLI --help / docs/cli/agent.md 核验）：
``openclaw agent --local --json --model <provider/model 或裸 model>
[--thinking level] --session-id <session> --timeout <s> --message-file <prompt>``。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent_runtime.catalog import get_adapter
from server.app.workflows.pi_protocol import PROMPT_INSTRUCTION

MANIFEST = {
    "job_id": "job-1",
    "node_key": "gen",
    "capability": "generate",
    "runtime": "openclaw",
    "inputs": ["a.txt"],
    "expected_outputs": ["out.json"],
    "tools": ["read", "write"],
    "config": {},
    "execution": {
        "binary": "openclaw",
        "provider": "kimi",
        "model": "kimi-code",
        "thinking": "low",
        "timeout_seconds": 1800,
        "no_sandbox": False,
    },
}

KW = {
    "skill_dir": Path("/skill"),
    "session_dir": Path("/session"),
    "session_name": "job-1:gen:tok",
    "prompt_file": Path("/prompt.md"),
}

PI_VELITES_ONLY_FLAGS = (
    "--mode",
    "--skill",
    "--tools",
    "--no-context-files",
    "--no-extensions",
    "--no-prompt-templates",
    "--no-skills",
    "--approve",
    "--require-output",
    "--timeout-seconds",
)


def _dispatch(manifest: dict) -> list[str]:
    runtime = str(manifest.get("runtime") or "").strip()
    return get_adapter(runtime).build_command(manifest, prompt_instruction=PROMPT_INSTRUCTION, **KW)


def _execution(manifest: dict, **patch: object) -> dict:
    return {**manifest, "execution": {**MANIFEST["execution"], **patch}}


def test_openclaw_command_exact_argv() -> None:
    cmd = _dispatch(MANIFEST)
    assert cmd == [
        "openclaw",
        "agent",
        "--local",
        "--json",
        "--model",
        "kimi/kimi-code",
        "--session-id",
        "job-1:gen:tok",
        "--message-file",
        "/prompt.md",
        "--thinking",
        "low",
        "--timeout",
        "1800",
    ]
    for flag in PI_VELITES_ONLY_FLAGS:
        assert flag not in cmd


def test_openclaw_command_bare_model_without_provider() -> None:
    cmd = _dispatch(_execution(MANIFEST, provider=""))
    assert cmd[cmd.index("--model") + 1] == "kimi-code"


def test_openclaw_command_omits_empty_thinking_and_bad_timeout() -> None:
    cmd = _dispatch(_execution(MANIFEST, thinking="", timeout_seconds=0))
    assert "--thinking" not in cmd
    assert "--timeout" not in cmd
    # timeout_seconds=0 时也不发 --timeout（CLI 默认 600 兜底）。
    bare = _dispatch(_execution(MANIFEST, timeout_seconds="600"))
    assert "--timeout" not in bare


def test_openclaw_command_binary_override() -> None:
    cmd = _dispatch(_execution(MANIFEST, binary="/opt/bin/openclaw-dev"))
    assert cmd[0] == "/opt/bin/openclaw-dev"


def test_openclaw_render_command_spec_uses_placeholders() -> None:
    from server.app.workflows.pi_protocol import render_command_spec

    command = render_command_spec(MANIFEST)["command"]
    assert command[0] == "openclaw"
    assert "{session_name}" in command
    assert any("{prompt_file}" in part for part in command)
    assert "--local" in command and "--json" in command


def test_openclaw_ignores_velites_budget_config() -> None:
    # max_turns/max_tokens 是 velites 专属预算键：openclaw 不强行映射。
    manifest = {**MANIFEST, "config": {"max_turns": 8, "max_tokens": 120000}}
    cmd = _dispatch(manifest)
    assert "--max-turns" not in cmd
    assert "--max-tokens" not in cmd


def test_openclaw_dispatch_via_catalog_rejects_unknown_runtime() -> None:
    manifest = {**MANIFEST, "runtime": "rust"}
    with pytest.raises(ValueError, match="unknown agent runtime"):
        _dispatch(manifest)
