"""velites runtime：命令构建、render_command_spec 占位符、runtime 分发。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from server.app.agent_broker.dispatch import resolve_execution_block
from server.app.executors.runtime_config import PiRuntimeConfig
from server.app.workflows.pi_command_builder import build_pi_command
from server.app.workflows.pi_config import PiConfig
from server.app.workflows.pi_protocol import (
    PROMPT_INSTRUCTION,
    build_command,
    render_command_spec,
)
from server.app.workflows.schema import WorkflowNode
from server.app.workflows.velites_command import build_command_for_flavor

MANIFEST = {
    "job_id": "job-1",
    "node_key": "gen",
    "capability": "generate",
    "runtime": "velites",
    "inputs": ["a.txt"],
    "expected_outputs": ["out.json", "report.json"],
    "tools": ["read", "write"],
    "config": {},
    "execution": {
        "binary": "velites",
        "provider": "gateway",
        "model": "kimi-k2.6",
        "thinking": "low",
        "timeout_seconds": 300,
        "no_sandbox": False,
    },
}

KW = {
    "skill_dir": Path("/skill"),
    "session_dir": Path("/session"),
    "session_name": "job-1:gen:tok",
    "prompt_file": Path("/prompt.md"),
}

PI_ONLY_FLAGS = (
    "--no-context-files",
    "--no-extensions",
    "--no-prompt-templates",
    "--no-skills",
    "--approve",
)


def _dispatch(manifest: dict) -> list[str]:
    return build_command_for_flavor(
        manifest,
        prompt_instruction=PROMPT_INSTRUCTION,
        pi_fallback=build_command,
        **KW,
    )


def _execution(manifest: dict, **patch: object) -> dict:
    return {**manifest, "execution": {**MANIFEST["execution"], **patch}}


def test_runtime_config_flavor_defaults_to_pi() -> None:
    config = PiRuntimeConfig()
    assert config.flavor == "pi"
    assert config.binary == "pi"


def test_runtime_config_velites_defaults_binary() -> None:
    config = PiRuntimeConfig.model_validate({"flavor": "velites"})
    assert config.flavor == "velites"
    assert config.binary == "velites"


def test_runtime_config_rejects_unknown_flavor() -> None:
    with pytest.raises(ValidationError):
        PiRuntimeConfig.model_validate({"flavor": "rust"})


def test_velites_command_exact_argv() -> None:
    cmd = _dispatch(MANIFEST)
    assert cmd == [
        "velites",
        "--mode",
        "json",
        "--session-dir",
        "/session",
        "--name",
        "job-1:gen:tok",
        "--skill",
        "/skill",
        "--tools",
        "read,write",
        "--provider",
        "gateway",
        "--model",
        "kimi-k2.6",
        "--thinking",
        "low",
        "--timeout-seconds",
        "300",
        "--require-output",
        "out.json",
        "--require-output",
        "report.json",
        "@/prompt.md",
        "Execute the attached node instructions.",
    ]
    for flag in PI_ONLY_FLAGS:
        assert flag not in cmd


def test_velites_command_maps_named_provider_to_gateway() -> None:
    # pi 命名 provider（deepseek 等）在 velites 侧收敛为 gateway 单出口；
    # 协议名原样透传。
    named = _execution(MANIFEST, provider="deepseek")
    cmd = _dispatch(named)
    assert cmd[cmd.index("--provider") + 1] == "gateway"
    for native in ("gateway", "openai_compat", "stub"):
        passthrough = _execution(MANIFEST, provider=native)
        assert _dispatch(passthrough)[cmd.index("--provider") + 1] == native


def test_velites_command_budget_flags_from_node_config() -> None:
    manifest = {**MANIFEST, "config": {"max_turns": 8, "max_tokens": 120000}}
    cmd = _dispatch(manifest)
    assert cmd[cmd.index("--max-turns") + 1] == "8"
    assert cmd[cmd.index("--max-tokens") + 1] == "120000"
    # 节点未配置预算 → 不发 flag（不硬编码默认值）。
    bare = _dispatch(MANIFEST)
    assert "--max-turns" not in bare
    assert "--max-tokens" not in bare


def test_velites_command_omits_empty_optional_flags() -> None:
    manifest = _execution(MANIFEST, provider="", model="", thinking="", timeout_seconds=0)
    cmd = _dispatch(manifest)
    assert "--provider" not in cmd
    assert "--model" not in cmd
    assert "--thinking" not in cmd
    assert "--timeout-seconds" not in cmd


def test_runtime_dispatch_defaults_fail_fast_without_runtime() -> None:
    manifest = {k: v for k, v in MANIFEST.items() if k != "runtime"}
    with pytest.raises(ValueError, match="unknown agent runtime"):
        _dispatch(manifest)


def test_runtime_dispatch_rejects_unknown_runtime() -> None:
    manifest = {**MANIFEST, "runtime": "rust"}
    with pytest.raises(ValueError, match="unknown agent runtime"):
        _dispatch(manifest)


def test_velites_no_sandbox_escape_hatch() -> None:
    # 默认不发 --no-sandbox（沙箱开启）。
    assert "--no-sandbox" not in _dispatch(MANIFEST)
    manifest = _execution(MANIFEST, no_sandbox=True)
    assert "--no-sandbox" in _dispatch(manifest)


def test_render_command_spec_velites_uses_placeholders() -> None:
    spec = render_command_spec(MANIFEST)
    assert spec["version"] == 1
    command = spec["command"]
    assert command[0] == "velites"
    assert "{session_dir}" in command
    assert "{session_name}" in command
    assert any("{skill_dir}" in part for part in command)
    assert "@{prompt_file}" in command
    for flag in PI_ONLY_FLAGS:
        assert flag not in command
    assert command[command.index("--require-output") + 1] == "out.json"


def test_render_command_spec_pi_runtime_uses_pi_argv() -> None:
    manifest = _execution({**MANIFEST, "runtime": "pi"}, binary="pi")
    command = render_command_spec(manifest)["command"]
    assert command[0] == "pi"
    assert "--approve" in command


# --- execution 解析（EXEC-RUNTIME-DISPATCH-001）：runtime 钉死命令构建器 ---


def _node(provider: str = "", model: str = "", thinking: str = "") -> WorkflowNode:
    from server.app.workflows.schema import WorkflowNodeExecution

    return WorkflowNode(
        key="gen",
        label="gen",
        capability="generate",
        execution=WorkflowNodeExecution(provider=provider, model=model, thinking=thinking),
    )


@pytest.mark.no_db
def test_resolve_execution_node_override_wins() -> None:
    workspace = {
        "default_agent_provider": "ws-provider",
        "default_agent_model": "ws-model",
        "default_agent_thinking": "high",
    }
    block = resolve_execution_block(
        _node("node-provider", "node-model", "low"), workspace, "velites"
    )
    assert block == {
        "binary": "velites",
        "provider": "node-provider",
        "model": "node-model",
        "thinking": "low",
        "timeout_seconds": 1800,
        "no_sandbox": False,
    }


@pytest.mark.no_db
def test_resolve_execution_falls_back_to_workspace_defaults() -> None:
    workspace = {"default_agent_provider": "ws-provider", "default_agent_model": "ws-model"}
    block = resolve_execution_block(_node(), workspace, "pi")
    assert block["binary"] == "pi"
    assert block["provider"] == "ws-provider"
    assert block["model"] == "ws-model"
    assert block["thinking"] == ""


@pytest.mark.no_db
def test_resolve_execution_requires_provider_and_model() -> None:
    with pytest.raises(ValueError, match="requires a provider"):
        resolve_execution_block(_node(model="m"), {}, "velites")
    with pytest.raises(ValueError, match="requires a model"):
        resolve_execution_block(_node(provider="p"), {}, "velites")


@pytest.mark.no_db
def test_resolve_execution_fails_fast_on_unknown_runtime() -> None:
    workspace = {"default_agent_provider": "p", "default_agent_model": "m"}
    with pytest.raises(ValueError, match=r"supported runtimes: pi, velites"):
        resolve_execution_block(_node(), workspace, "openclaw")


# --- 保留的本地 pi runner 链（PiConfig flavor 解析，不经 broker manifest） ---


def test_piconfig_from_config_flavor_parsing() -> None:
    assert PiConfig.from_config({"binary": "pi"}).flavor == "pi"
    velites = PiConfig.from_config({"binary": "pi", "flavor": "velites"})
    assert velites.flavor == "velites"
    assert velites.binary == "velites"
    with pytest.raises(ValueError, match="flavor"):
        PiConfig.from_config({"binary": "pi", "flavor": "rust"})


def test_build_pi_command_velites_flavor() -> None:
    config = PiConfig(
        binary="velites",
        flavor="velites",
        provider="gateway",
        model="kimi-k2.6",
        thinking="low",
        timeout_seconds=300,
    )
    cmd = build_pi_command(
        config,
        tools=["read", "write"],
        expected_outputs=["out.json"],
        node_config={"max_turns": 4},
        **KW,
    )
    assert cmd[0] == "velites"
    assert cmd[cmd.index("--timeout-seconds") + 1] == "300"
    assert cmd[cmd.index("--require-output") + 1] == "out.json"
    assert cmd[cmd.index("--max-turns") + 1] == "4"
    for flag in PI_ONLY_FLAGS:
        assert flag not in cmd
