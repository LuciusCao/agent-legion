"""Worker-path output validation: resolve the manifest's pinned skill to a
commit in the main process, then run materialization + validation on the
result-validate process pool (#569; #443 split this out of
``output_validation`` for the file-size budget).

Worker-reported success is untrusted: the Host revalidates server-side after
unpacking the result archive, against the exact skill content the execution
used (#330). Materialization goes through the shared (skill, commit) cache
(``skills.commit_cache``) plus a per-validation private copy (PR #571 codex
P1s: the shared tree is read-only, validators write only into their copy),
so ``cleanup_execution`` remains the per-validation cleanup of this path.

The validator never sees the raw job dir (#757): the pool task builds the
declared validation view (``validation_view``) — this node's declared inputs
from the job dir plus this attempt's declared outputs from the run's read
view. Sibling outputs and stale residues cannot enter the view, and the
view's exit arms reconcile output mutations back and enforce the inputs
read-only contract.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.result_validate_pool import (
    validate_in_pool,
    validate_skill_commit_outputs,
)
from server.app.skills.commit_cache import resolve_skill_commit

if TYPE_CHECKING:
    from server.app.skills.manager import SkillManager


def validate_worker_outputs(
    skill_manager: SkillManager,
    manifest: dict[str, Any],
    job_dir: Path,
    run_view_dir: Path,
) -> str | None:
    """Validate this attempt's outputs against the manifest's pinned skill.

    ``job_dir`` supplies the declared inputs (and parents the view scratch
    dir); ``run_view_dir`` is this attempt's read view supplying the
    declared outputs — the pool task builds the declared validation view
    from the two. Worker-reported success is untrusted; same bar as the
    local path.
    """
    skill = str(manifest.get("skill", ""))
    if not skill:
        return None
    try:
        commit = _manifest_commit(skill_manager, manifest, skill)
        verdict: str | None = validate_in_pool(
            validate_skill_commit_outputs,
            str(skill_manager.base_dir),
            str(skill_manager.runs_dir),
            tuple(skill_manager.git_command),
            skill,
            commit,
            str(job_dir),
            str(run_view_dir),
            tuple(str(name) for name in manifest.get("inputs") or ()),
            tuple(str(name) for name in manifest.get("expected_outputs") or ()),
        )
        return verdict
    except Exception as exc:
        # #204 broad-except audit: convert-to-contract, same channel as
        # run_output_validator's catch — the string verdict is the only
        # failure channel. The surface spans the commit resolution (the
        # DB-backed lock store, live-HEAD rev-parse), the pool hop (a broken
        # pool after the single rebuild-retry), and the pool task's
        # failures pickled back by reference — materialization/contract
        # (SkillRepoError, ValueError) plus the #757 view arms (an
        # unbuildable view, a failed output reconcile, a mutated declared
        # input all fail closed like an unrunnable validator) — a
        # Worker-pinned skill that cannot be
        # materialized or validated is an untrusted-input outcome, not a
        # host bug, and must fail THIS node ("Validator error: ...") rather
        # than crash the completion path — the lease would otherwise expire
        # into the same poison manifest. The exception text rides the
        # message.
        return f"Validator error: {exc}"


def _manifest_commit(skill_manager: SkillManager, manifest: dict[str, Any], skill: str) -> str:
    # Main-process pin resolution (may touch the DB-backed lock document —
    # never in a pool worker): #330 manifests carry the full skill_commit,
    # which wins outright; legacy manifests resolve skill_ref (empty =
    # latest, #322).
    commit = str(manifest.get("skill_commit", ""))
    if commit:
        return commit
    return resolve_skill_commit(skill_manager, skill, str(manifest.get("skill_ref", "")) or None)
