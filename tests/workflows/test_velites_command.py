"""velites flavor：配置解析、命令构建、render_command_spec 占位符（M4）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from server.app.executors.runtime_config import PiRuntimeConfig
from server.app.workflows.pi_command_builder import build_pi_command
from server.app.workflows.pi_config import PiConfig
from server.app.workflows.pi_protocol import (
    PROMPT_INSTRUCTION,
    build_command,
    render_command_spec,
)
from server.app.workflows.velites_command import build_command_for_flavor

MANIFEST = {
    "job_id": "job-1",
    "node_key": "gen",
    "capability": "generate",
    "inputs": ["a.txt"],
    "expected_outputs": ["out.json", "report.json"],
    "tools": ["read", "write"],
    "config": {},
    "pi": {
        "binary": "velites",
        "flavor": "velites",
        "provider": "gateway",
        "model": "kimi-k2.6",
        "thinking": "low",
        "timeout_seconds": 300,
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


def test_runtime_config_flavor_defaults_to_pi() -> None:
    config = PiRuntimeConfig()
    assert config.flavor == "pi"
    assert config.binary == "pi"


def test_runtime_config_velites_defaults_binary() -> None:
    config = PiRuntimeConfig.model_validate({"flavor": "velites"})
    assert config.flavor == "velites"
    assert config.binary == "velites"


def test_runtime_config_velites_keeps_explicit_binary() -> None:
    config = PiRuntimeConfig.model_validate({"flavor": "velites", "binary": "/opt/v"})
    assert config.binary == "/opt/v"


def test_runtime_config_rejects_unknown_flavor() -> None:
    with pytest.raises(ValidationError):
        PiRuntimeConfig.model_validate({"flavor": "rust"})


def test_piconfig_from_runtime_propagates_flavor() -> None:
    config = PiConfig.from_runtime(PiRuntimeConfig.model_validate({"flavor": "velites"}))
    assert config.flavor == "velites"
    assert config.binary == "velites"


def test_piconfig_from_config_flavor_parsing() -> None:
    assert PiConfig.from_config({"binary": "pi"}).flavor == "pi"
    velites = PiConfig.from_config({"binary": "pi", "flavor": "velites"})
    assert velites.flavor == "velites"
    assert velites.binary == "velites"
    with pytest.raises(ValueError, match="flavor"):
        PiConfig.from_config({"binary": "pi", "flavor": "rust"})


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
    manifest = {
        **MANIFEST,
        "pi": {"binary": "velites", "flavor": "velites", "timeout_seconds": 0},
    }
    cmd = _dispatch(manifest)
    assert "--provider" not in cmd
    assert "--model" not in cmd
    assert "--thinking" not in cmd
    assert "--timeout-seconds" not in cmd


def test_flavor_dispatch_defaults_to_pi() -> None:
    manifest = {**MANIFEST, "pi": {**MANIFEST["pi"], "flavor": "pi"}}
    assert _dispatch(manifest) == build_command(manifest, **KW)
    no_flavor = {**MANIFEST, "pi": {k: v for k, v in MANIFEST["pi"].items() if k != "flavor"}}
    assert _dispatch(no_flavor) == build_command(no_flavor, **KW)


def test_flavor_dispatch_rejects_unknown_flavor() -> None:
    manifest = {**MANIFEST, "pi": {**MANIFEST["pi"], "flavor": "rust"}}
    with pytest.raises(ValueError, match="unknown pi flavor"):
        _dispatch(manifest)


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
