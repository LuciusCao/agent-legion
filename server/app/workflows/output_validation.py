"""Deterministic output validation for skill-produced artifacts.

Agent output is untrusted regardless of where the agent ran: the node skill's
``scripts/validate_output.py`` is the contract enforcer (review skills fail
the node when the reviewer rejected items). Both the local Pi runner and the
Agent Worker completion path must run it after the agent finishes — the
Worker path has no runner-side hook, so the Host validates after unpacking
the result archive.
"""

from __future__ import annotations

import subprocess
import sys
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.executors._pi_skill import resolve_skill_dir

if TYPE_CHECKING:
    from server.app.skills.manager import SkillManager

VALIDATOR_TIMEOUT_SECONDS = 30


def run_output_validator(
    skill_dir: Path,
    job_dir: Path,
    *,
    timeout_seconds: int = VALIDATOR_TIMEOUT_SECONDS,
) -> str | None:
    """Run the skill's ``validate_output.py`` against ``job_dir``.

    Returns ``None`` when outputs are valid (or the skill has no validator);
    otherwise the error message to record on the failed run.
    """
    validator = skill_dir / "scripts" / "validate_output.py"
    if not validator.is_file():
        return None
    try:
        proc = subprocess.run(
            [sys.executable, str(validator), str(job_dir)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return f"Validator error: {exc}"
    if proc.returncode != 0:
        return f"Output validation failed: {proc.stderr.strip()}"
    return None


def validate_worker_outputs(
    skill_manager: SkillManager,
    manifest: dict[str, Any],
    job_dir: Path,
) -> str | None:
    """Validate an Agent Worker result Host-side against the manifest's skill.

    Worker-reported success is untrusted: resolve the node skill pinned in the
    manifest and run its validator against the unpacked job directory, exactly
    like the local Pi runner does after process exit.
    """
    skill = str(manifest.get("skill", ""))
    if not skill:
        return None
    validation_id = f"validate-{uuid.uuid4().hex}"
    try:
        skill_dir = resolve_skill_dir(skill_manager, skill, validation_id)
    except Exception as exc:
        return f"Validator error: {exc}"
    try:
        return run_output_validator(skill_dir, job_dir)
    finally:
        skill_manager.cleanup_execution(validation_id)
