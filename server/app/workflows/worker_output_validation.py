"""Worker-path output validation: materialize the manifest's pinned skill and
run the two-layer validator against the unpacked job dir (#443 split out of
``output_validation`` for the file-size budget).

Worker-reported success is untrusted: the Host revalidates server-side after
unpacking the result archive, against the exact skill content the execution
used (#330).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.skills.checkout import resolve_skill_checkout, validate_run_dir
from server.app.workflows.output_validation import run_output_validator

if TYPE_CHECKING:
    from server.app.skills.manager import SkillManager


def validate_worker_outputs(
    skill_manager: SkillManager,
    manifest: dict[str, Any],
    job_dir: Path,
) -> str | None:
    """Run the manifest skill's validator against the unpacked Worker job dir
    (Worker-reported success is untrusted; same bar as the local path)."""
    skill = str(manifest.get("skill", ""))
    if not skill:
        return None
    validation_id = f"validate-{uuid.uuid4().hex}"
    try:
        run_dir = _manifest_run_dir(skill_manager, manifest, skill, validation_id)
    except Exception as exc:
        # #204 broad-except audit: convert-to-contract, same channel as
        # run_output_validator's catch — the string verdict is the only
        # failure channel. _manifest_run_dir spans the skill materialization
        # surface (git archive export, the DB-backed lock store, the contract
        # validation's ValueError family) plus its own cleanup-guard
        # re-raise; a Worker-pinned skill name that cannot be materialized is
        # an untrusted-input outcome, not a host bug, and must fail THIS node
        # ("Validator error: ...") rather than crash the completion path —
        # the lease would otherwise expire into the same poison manifest.
        # The exception text rides the message.
        return f"Validator error: {exc}"
    try:
        return run_output_validator(run_dir, job_dir)
    finally:
        skill_manager.cleanup_execution(validation_id)


def _manifest_run_dir(
    skill_manager: SkillManager, manifest: dict[str, Any], skill: str, validation_id: str
) -> Path:
    """Materialize the manifest's skill content for validation (#330)."""
    # Manifests written since #330 record the full skill_commit: re-resolve it
    # exactly — a commit id is immune to HEAD moves (latest nodes) and to
    # retagging (pinned nodes), so the validator always matches the executed
    # skill content. Legacy manifests without it fall back to re-resolving
    # skill_ref (empty = latest, the #322 semantics).
    commit = str(manifest.get("skill_commit", ""))
    if commit:
        run_dir = skill_manager.checkout_skill_commit(skill, validation_id, commit)
        validate_run_dir(skill_manager, skill, validation_id, run_dir)
        return run_dir
    ref = str(manifest.get("skill_ref", ""))
    return resolve_skill_checkout(skill_manager, skill, validation_id, ref).run_dir
