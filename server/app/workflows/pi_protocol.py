"""Shared Agent protocol builders: prompt, command, model-error scanning.

Single server-side implementation consumed by the local Pi runner and Agent
Worker bundle builder. Command lines are persisted and shipped to workers, so
no credentials ever become part of a command spec.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from server.app.agent_runtime.catalog import get_adapter
from server.app.workflows.node_prompt import build_node_instructions
from shared.pi_model_error import detect_model_error, fold_model_error

__all__ = [
    "JOB_DIR_PLACEHOLDER",
    "PROMPT_FILE_PLACEHOLDER",
    "PROMPT_INSTRUCTION",
    "SESSION_DIR_PLACEHOLDER",
    "SESSION_NAME_PLACEHOLDER",
    "SKILL_DIR_PLACEHOLDER",
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
    """Fixed platform envelope plus exactly one node-instructions section.

    The envelope (job/skill paths, validator, declared IO, output discipline)
    never varies; the closing section semantics live in
    ``node_prompt.build_node_instructions``.
    """
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
        "",
        "Node instructions:",
        build_node_instructions(manifest),
    ]
    return "\n".join(lines) + "\n"


def render_command_spec(manifest: dict[str, Any]) -> dict[str, Any]:
    """Render prompt/command with path placeholders for Agent bundle shipping.

    The command argv comes from the runtime adapter registered for
    ``manifest["runtime"]`` (``server/app/agent_runtime`` catalog); the
    placeholder mechanism is runtime-neutral and stays here so the adapter
    layer never imports this module (no import cycle).
    """
    job_dir = Path(JOB_DIR_PLACEHOLDER)
    skill_dir = Path(SKILL_DIR_PLACEHOLDER)
    runtime = str(manifest.get("runtime") or "").strip()
    return {
        "version": 1,
        "prompt": build_prompt(manifest, job_dir=job_dir, skill_dir=skill_dir),
        "command": get_adapter(runtime).build_command(
            manifest,
            skill_dir=skill_dir,
            session_dir=Path(SESSION_DIR_PLACEHOLDER),
            session_name=SESSION_NAME_PLACEHOLDER,
            prompt_file=Path(PROMPT_FILE_PLACEHOLDER),
            prompt_instruction=PROMPT_INSTRUCTION,
        ),
        "prompt_instruction": PROMPT_INSTRUCTION,
    }
