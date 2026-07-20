"""Shared Pi protocol builders: prompt, command, model-error scanning.

Single server-side implementation consumed by the local Pi runner (via the
legacy wrappers in pi_prompt.py / pi_command_builder.py / pi_runner.py), the
remote executor payload builder, and the remote worker. ``environment`` from
``manifest["pi"]`` is deliberately never part of a command spec: it may carry
API keys, and command lines are persisted and shipped to workers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROMPT_INSTRUCTION = "Execute the attached node instructions."

# Placeholders used by render_command_spec so workers can substitute local paths.
JOB_DIR_PLACEHOLDER = "{job_dir}"
SKILL_DIR_PLACEHOLDER = "{skill_dir}"
SESSION_DIR_PLACEHOLDER = "{session_dir}"
SESSION_NAME_PLACEHOLDER = "{session_name}"
PROMPT_FILE_PLACEHOLDER = "{prompt_file}"


def build_prompt(manifest: dict[str, Any], *, job_dir: Path, skill_dir: Path) -> str:
    lines = [
        "Execute the loaded node skill for this Video Hive workflow job.",
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
    pi = manifest["pi"]
    cmd: list[str] = [
        str(pi.get("binary") or "pi"),
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
        value = str(pi.get(key) or "")
        if value:
            cmd.extend([flag, value])
    cmd.extend([f"@{prompt_file}", PROMPT_INSTRUCTION])
    return cmd


def detect_model_error(events_file: Path) -> str | None:
    """Scan Pi JSONL events for model-call failures reported by the CLI.

    Pi can exit with code 0 even when the upstream model request fails
    (e.g. a 400 from the provider). In that case the events file contains
    assistant messages whose ``stopReason`` is ``error`` and which carry an
    ``errorMessage``. Detecting this prevents us from reporting a misleading
    "Missing outputs" error when the agent never had a chance to run.

    Pi auto-retries transient failures (e.g. "terminated"), so an error only
    counts when no later assistant message succeeds; recovered retries pass.
    """
    if not events_file.is_file():
        return None
    last_error: str | None = None
    try:
        with events_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                messages: list[dict[str, Any]] = []
                # message_start / message_end / turn_end wrap the assistant msg
                msg = event.get("message") or {}
                if not isinstance(msg, dict):
                    turn_end = event.get("turn_end") or {}
                    msg = turn_end.get("message") if isinstance(turn_end, dict) else {}
                if isinstance(msg, dict):
                    messages.append(msg)

                # message_update events nest under assistantMessageEvent
                assistant_event = event.get("assistantMessageEvent") or {}
                if isinstance(assistant_event, dict):
                    nested = assistant_event.get("message") or {}
                    if isinstance(nested, dict):
                        messages.append(nested)

                for msg in messages:
                    if msg.get("errorMessage"):
                        last_error = str(msg["errorMessage"])
                    elif msg.get("stopReason") in ("stop", "toolUse"):
                        last_error = None
    except Exception:
        return None
    return last_error


def render_command_spec(manifest: dict[str, Any]) -> dict[str, Any]:
    """Render prompt/command with path placeholders for remote payload shipping.

    The returned spec never includes ``pi.environment``; workers merge env from
    the manifest themselves.
    """
    job_dir = Path(JOB_DIR_PLACEHOLDER)
    skill_dir = Path(SKILL_DIR_PLACEHOLDER)
    return {
        "version": 1,
        "prompt": build_prompt(manifest, job_dir=job_dir, skill_dir=skill_dir),
        "command": build_command(
            manifest,
            skill_dir=skill_dir,
            session_dir=Path(SESSION_DIR_PLACEHOLDER),
            session_name=SESSION_NAME_PLACEHOLDER,
            prompt_file=Path(PROMPT_FILE_PLACEHOLDER),
        ),
        "prompt_instruction": PROMPT_INSTRUCTION,
    }
