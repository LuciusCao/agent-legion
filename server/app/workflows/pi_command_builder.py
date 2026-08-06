from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from server.app.workflows.pi_config import PiConfig
from server.app.workflows.pi_protocol import PROMPT_INSTRUCTION, build_command
from server.app.workflows.velites_command import build_command_for_flavor


def build_pi_command(
    config: PiConfig,
    *,
    skill_dir: Path,
    session_dir: Path,
    tools: list[str],
    session_name: str,
    prompt_file: Path,
    expected_outputs: Iterable[str] = (),
    node_config: Mapping[str, Any] | None = None,
) -> list[str]:
    return build_command_for_flavor(
        {
            "tools": tools,
            "expected_outputs": list(expected_outputs),
            "config": dict(node_config or {}),
            # Legacy local-runner adapter: PiConfig.flavor is the runtime here.
            "runtime": config.flavor,
            "execution": {
                "binary": config.binary,
                "provider": config.provider,
                "model": config.model,
                "thinking": config.thinking,
                "timeout_seconds": config.timeout_seconds,
                "no_sandbox": config.velites_no_sandbox,
            },
        },
        skill_dir=skill_dir,
        session_dir=session_dir,
        session_name=session_name,
        prompt_file=prompt_file,
        prompt_instruction=PROMPT_INSTRUCTION,
        pi_fallback=build_command,
    )
