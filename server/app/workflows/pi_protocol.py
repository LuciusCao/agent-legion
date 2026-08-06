"""Shared Agent protocol builders: prompt, command, model-error scanning.

Single server-side implementation consumed by the local Pi runner and Agent
Worker bundle builder. Command lines are persisted and shipped to workers, so
no credentials ever become part of a command spec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.workflows.velites_command import build_command_for_flavor
from shared.pi_model_error import detect_model_error, fold_model_error

__all__ = [
    "JOB_DIR_PLACEHOLDER",
    "PROMPT_FILE_PLACEHOLDER",
    "PROMPT_INSTRUCTION",
    "SESSION_DIR_PLACEHOLDER",
    "SESSION_NAME_PLACEHOLDER",
    "SKILL_DIR_PLACEHOLDER",
    "build_command",
    "build_prompt",
    "detect_model_error",
    "fold_model_error",
    "render_command_spec",
]

PROMPT_INSTRUCTION = "Execute the attached node instructions."

# Placeholders used by render_command_spec so workers can substitute local paths.
JOB_DIR_PLACEHOLDER = "{job_dir}"
SKILL_DIR_PLACEHOLDER = "{skill_dir}"
SESSION_DIR_PLACEHOLDER = "{session_dir}"
SESSION_NAME_PLACEHOLDER = "{session_name}"
PROMPT_FILE_PLACEHOLDER = "{prompt_file}"


def build_prompt(manifest: dict[str, Any], *, job_dir: Path, skill_dir: Path) -> str:
    lines = [
        "Execute the loaded node skill for this Agent Legion workflow job.",
        "",
        f"Job ID: {manifest['job_id']}",
        f"Node: {manifest['node_key']}",
        f"Working directory: {job_dir}",
        f"Skill directory: {skill_dir}",
        f"Validator script: {skill_dir / 'scripts' / 'validate_output.py'}",
        "",
        "Declared inputs:",
        *(f"- {item}" for item in manifest["inputs"]),
        "",
        "Required outputs:",
        *(f"- {item}" for item in manifest["expected_outputs"]),
        "",
        (
            "Write required outputs directly into the working directory. "
            "Never write outputs into the run/session directory (runs/); "
            "all declared outputs must live at the top level of the working directory. "
            "Do not modify inputs or create undeclared root-level artifacts. "
            "Do not read, search, or modify anything outside the working directory "
            "and the skill directory. "
            "Finish after all required outputs are written and correct."
        ),
    ]
    additional = str(manifest.get("additional_prompt", "")).strip()
    if additional:
        lines.extend(["", "Additional node instructions:", additional])
    return "\n".join(lines) + "\n"


def build_command(
    manifest: dict[str, Any],
    *,
    skill_dir: Path,
    session_dir: Path,
    session_name: str,
    prompt_file: Path,
) -> list[str]:
    execution = manifest["execution"]
    cmd: list[str] = [
        str(execution.get("binary") or "pi"),
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
        ",".join(manifest["tools"]),
        "--approve",
    ]
    for flag, key in (("--provider", "provider"), ("--model", "model"), ("--thinking", "thinking")):
        value = str(execution.get(key) or "")
        if value:
            cmd.extend([flag, value])
    cmd.extend([f"@{prompt_file}", PROMPT_INSTRUCTION])
    return cmd


def render_command_spec(manifest: dict[str, Any]) -> dict[str, Any]:
    """Render prompt/command with path placeholders for Agent bundle shipping.

    The command argv follows ``manifest["runtime"]`` (pi | velites); the
    placeholder mechanism is runtime-neutral.
    """
    job_dir = Path(JOB_DIR_PLACEHOLDER)
    skill_dir = Path(SKILL_DIR_PLACEHOLDER)
    return {
        "version": 1,
        "prompt": build_prompt(manifest, job_dir=job_dir, skill_dir=skill_dir),
        "command": build_command_for_flavor(
            manifest,
            skill_dir=skill_dir,
            session_dir=Path(SESSION_DIR_PLACEHOLDER),
            session_name=SESSION_NAME_PLACEHOLDER,
            prompt_file=Path(PROMPT_FILE_PLACEHOLDER),
            prompt_instruction=PROMPT_INSTRUCTION,
            pi_fallback=build_command,
        ),
        "prompt_instruction": PROMPT_INSTRUCTION,
    }
