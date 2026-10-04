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
plus this attempt's declared outputs from the run's read view. Sibling
outputs and stale residues cannot enter the view, and the view's exit arms
reconcile output mutations back and enforce the inputs read-only contract.
Input bytes resolve to the dispatch-frozen CAS copy when the manifest
carries ``input_artifacts`` refs (#828/#830/#833), falling back to the job
dir; only the picklable CAS root and refs map cross the pool boundary, the
blob open itself happens in the pool worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent_broker.result_validate_pool import (
    validate_in_pool,
    validate_skill_commit_outputs,
)
from server.app.skills.commit_cache import resolve_skill_commit
from server.app.workflows.remote_output_guard import (
    find_remote_output_rewrites,
    remote_output_rewrite_error,
)

if TYPE_CHECKING:
    from server.app.services.artifact_store import ArtifactStore
    from server.app.skills.manager import SkillManager


def validate_worker_outputs(
    skill_manager: SkillManager,
    manifest: dict[str, Any],
    job_dir: Path,
    run_view_dir: Path,
    artifact_store: ArtifactStore | None = None,
    read_only_outputs: Mapping[str, str] | None = None,
) -> str | None:
    """Validate this attempt's outputs against the manifest's pinned skill.

    ``job_dir`` supplies the declared inputs (and parents the view scratch
    dir); ``run_view_dir`` is this attempt's read view supplying the
    declared outputs — the pool task builds the declared validation view
    from the two. ``artifact_store`` contributes only its CAS root (the
    dispatch-frozen input bytes channel, #833); None (or a legacy manifest
    without ``input_artifacts``) means the job-dir fallback on every input.
    Worker-reported success is untrusted; same bar as the local path.
    ``read_only_outputs`` maps the landed remote-channel outputs to the
    digest the promote phase registered (#867, ``remote_output_guard``):
    re-hashed once after a passing validation, any change fails the run.
    """
    skill = str(manifest.get("skill", ""))
    if not skill:
        return None
    try:
        commit = _manifest_commit(skill_manager, manifest, skill)
        refs = manifest.get("input_artifacts")
        if not isinstance(refs, dict):
            # Legacy manifest (or a claim-time non-dict shape): no CAS channel
            # at all — never touch the store, every input reads the job dir.
            refs, artifact_store = None, None
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
            refs,
            str(artifact_store.root) if artifact_store is not None else None,
        )
        if verdict is None and (
            rewrites := find_remote_output_rewrites(run_view_dir, read_only_outputs or {})
        ):
            verdict = remote_output_rewrite_error(rewrites)
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
        # input all fail closed like an unrunnable validator), and the #867
        # remote-output hashing (OSError on an unreadable landed file) — a
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
