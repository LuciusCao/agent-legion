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

The validator never sees the raw job dir (#757): it runs against the
declared validation view (``validation_view.validation_view``, the single
construction site) — this node's declared inputs from the job dir plus this
attempt's declared outputs from the run's read view. Sibling nodes' outputs
and stale same-name residues cannot enter, so a glob-based legacy validator
can no longer flip a clean run on another node's file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.result_validate_pool import (
    validate_in_pool,
    validate_skill_commit_outputs,
)
from server.app.skills.commit_cache import resolve_skill_commit
from server.app.workflows.validation_view import validation_view

if TYPE_CHECKING:
    from server.app.skills.manager import SkillManager


def validate_worker_outputs(
    skill_manager: SkillManager,
    manifest: dict[str, Any],
    job_dir: Path,
    run_view_dir: Path,
) -> str | None:
    """Run the manifest skill's validator against the declared validation view.

    ``job_dir`` supplies the declared inputs (and parents the scratch view);
    ``run_view_dir`` is this attempt's read view supplying the declared
    outputs. Worker-reported success is untrusted; same bar as the local path.
    """
    skill = str(manifest.get("skill", ""))
    if not skill:
        return None
    try:
        commit = _manifest_commit(skill_manager, manifest, skill)
        inputs = tuple(str(name) for name in manifest.get("inputs") or ())
        outputs = tuple(str(name) for name in manifest.get("expected_outputs") or ())
        with validation_view(
            job_dir, inputs=inputs, outputs=outputs, output_source=run_view_dir
        ) as view_dir:
            verdict: str | None = validate_in_pool(
                validate_skill_commit_outputs,
                str(skill_manager.base_dir),
                str(skill_manager.runs_dir),
                tuple(skill_manager.git_command),
                skill,
                commit,
                str(view_dir),
            )
        return verdict
    except Exception as exc:
        # #204 broad-except audit: convert-to-contract, same channel as
        # run_output_validator's catch — the string verdict is the only
        # failure channel. The surface spans the commit resolution (the
        # DB-backed lock store, live-HEAD rev-parse), the validation-view
        # construction (#757: an unbuildable view fails closed like an
        # unrunnable validator), the pool hop (a broken pool after the
        # single rebuild-retry), and the pool task's
        # materialization/contract failures pickled back by reference
        # (SkillRepoError, ValueError) — a Worker-pinned skill that cannot be
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
