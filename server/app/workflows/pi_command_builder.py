from __future__ import annotations

from pathlib import Path

from server.app.workflows.pi_config import PiConfig


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
    cmd: list[str] = [
        config.binary,
        "--mode",
        "json",
        "--session-dir",
        str(session_dir),
        "--name",
        session_name,
        "--no-context-files",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-skills",
        "--skill",
        str(skill_dir),
        "--tools",
        ",".join(tools),
        "--approve",
    ]
    for flag, value in (
        ("--provider", config.provider),
        ("--model", config.model),
        ("--thinking", config.thinking),
    ):
        if value:
            cmd.extend([flag, value])
    cmd.extend([f"@{prompt_file}", "Execute the attached node instructions."])
    return cmd
