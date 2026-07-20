from __future__ import annotations

from pathlib import Path

from server.app.workflows.pi_config import PiConfig
from server.app.workflows.pi_protocol import build_command


def build_pi_command(
    config: PiConfig,
    *,
    skill_dir: Path,
    session_dir: Path,
    tools: list[str],
    session_name: str,
    prompt_file: Path,
) -> list[str]:
    """Build the Pi CLI command for a workflow node run."""
    manifest = {
        "tools": tools,
        "pi": {
            "binary": config.binary,
            "provider": config.provider,
            "model": config.model,
            "thinking": config.thinking,
        },
    }
    return build_command(
        manifest,
        skill_dir=skill_dir,
        session_dir=session_dir,
        session_name=session_name,
        prompt_file=prompt_file,
    )
